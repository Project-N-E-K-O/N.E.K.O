# -*- coding: utf-8 -*-
"""
UserDirectivesManager — per-character store for explicit user ban-topic
directives ("别再提 X / stop saying X / その話はもう / ...").

设计动机
--------
本轮 LLM 看得到用户原话，不需要这里干预。但下一次 session 重启（archive
触发 / cold start / 重连），那句话早被 ``compress_history`` 抹掉，模型会再次
踩雷。所以把抽到的 term 持久化 3 天（``USER_DIRECTIVE_TTL_SECONDS``），
``_build_initial_prompt`` 启动时拼一段注入 system prompt 尾部。

设计要点
--------
- **分发入口**：``dispatch_user_utterance`` fan-out。本模块 import 时自动调
  ``register_user_utterance_sink``，与 ``plugin/core/state.py`` 同款 self-
  registration（dedup-on-identity，重复注册不会重复触发）。
- **抽取**：``config.prompts.prompts_directives.extract_directives`` 全 locale
  并行跑（用户中英混说常见）；命中后 term 经 ``_trim_term`` 清洗。
- **dedup 键**：``(kind, term.casefold())``。重复命中 → 刷新 ``last_seen_at`` /
  ``expire_at`` + ``hit_count += 1``；新条目入库。
- **存储**：``memory/{name}/user_directives.json``。schema 见 ``_DEFAULT_FILE``。
- **TTL**：每条记录的 ``expire_at = last_seen_at + USER_DIRECTIVE_TTL_SECONDS``。
  读取时过滤；``purge_expired`` 重写文件（可选，惰性即可）。
- **prompt 注入**：``render_prompt_block(name, lang)`` 返回拼好的字符串（含
  leading newline），空时返回 ""。调用方直接 ``prompt += ...``。
- **并发**：per-character ``threading.Lock``，模式照搬 ``memory/cursors.py``。

什么不抽取
----------
- 无对象的"闭嘴/换话题/shut up"：本轮已经在 context 里，模型看得到；持久化又
  没有具体话题，把这种意图塞到下一轮 prompt 里反而误伤。
- 普通陈述句"我不喜欢西瓜"：不是显式 ban-topic 指令，让 fact/persona 流水线
  处理偏好抽取。

错杀策略
--------
正则模板宽松。错杀代价 = 用户下次再说一次同义话；漏抽代价 = 用户被再次冒犯
所以倾向错杀。term 长度 ∈ [2, 40] 才入库，超界丢弃。
"""
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


def _normalize_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """把磁盘读上来的一条记录归一化为 dict；非法/缺字段 → None（丢弃）。

    历史兼容：早期可能只有 ``term`` 和 ``created_at``——这里补齐
    ``last_seen_at`` / ``expire_at`` / ``hit_count`` / ``kind`` / ``locale``。

    ⚠️ 容错：单条脏数据（如 ``created_at: "abc"``）不应该让整份文件 load 失败、
    把所有合法 directive 一起重置成空。整个函数包在一个 try/except 里，本条
    返回 None 让 caller 丢弃但保留其它条目（CodeRabbit Minor）。
    """
    if not isinstance(raw, dict):
        return None
    try:
        term = raw.get("term")
        if not isinstance(term, str) or not term:
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
    """Per-character ban-topic 存储（线程安全）。

    用法：
        mgr = UserDirectivesManager()
        mgr.record_from_text(lanlan_name, raw_user_text)
        block = mgr.render_prompt_block(lanlan_name, lang='zh')
        # 把 block 直接 concat 到 system prompt 末尾

    单进程内全局唯一实例 ``_GLOBAL_MANAGER``（见模块末尾）；sink 也基于它注册。
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
        """登记一条 directive；命中已有 ``(kind, term.casefold())`` 则刷新。

        返回最终存盘的 dict（含合并/刷新后的字段）。
        """
        if not name or not term:
            return {}
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
        """对一段 user 文本跑完整抽取 → 入库流水线。

        返回当次写入/刷新的 entry 列表（空 = 未命中任何 pattern）。
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
        """返回未过期、按 last_seen_at 倒序的最多 ``limit`` 条记录。"""
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
        """惰性清理：删除过期条目并落盘；返回删除条数。"""
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
        """把活跃 term 拼成 system prompt 片段。空时返回 ""。"""
        active = self.get_active(name, now=now)
        if not active:
            return ""
        terms = [e["term"] for e in active]
        return render_directives_block(terms, lang)

    def clear(self, name: str) -> None:
        """测试 / 用户手动清空入口。"""
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
    """user_utterance sink：抽取并落盘。错误吞掉（main_logic 已经在 dispatch
    内部做了 per-sink try/except，这里再加一层防御）。

    bucket = lanlan_name（dispatch 时 ``"default"`` 与角色名都会派一遍；用
    angle name 这一份）。
    """
    if not bucket or bucket == "default":
        # 角色名 bucket 在同一次 dispatch 里也会派；只处理一次避免重复入库
        return
    text = ""
    if isinstance(event, dict):
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
        get_user_directives_manager().record_from_text(bucket, text)
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
