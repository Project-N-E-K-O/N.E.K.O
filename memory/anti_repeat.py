# -*- coding: utf-8 -*-
"""
AntiRepeatCorpus — per-character rolling corpus + BM25 scorer 用于 AI 输出
的自动防复读（与用户行为无关）。

设计动机
--------
LLM 在连续生成 proactive chat 时容易反复绕回同一个 topic（"老虎再次出现"、
"我们再聊聊..."）。简单的 SequenceMatcher 相似度只能抓"完全重复"，对
"换一种说法但还在聊同一个 topic" 无效。

我们用 BM25：
- 背景 corpus = 最近 ``ANTI_REPEAT_BG_WINDOW`` 条 AI 输出，每条记 ngram set
- 前景 query = 最近 ``ANTI_REPEAT_FG_WINDOW`` 条（背景的子集，靠后那段）
- 新 draft 评分 = Σ BM25(term, fg) over draft 的 ngram
- 关键性质：高频公共词（"今天/觉得/哈哈/嗯"）DF 高 → IDF 低 → 几乎不打分；
  topic 词（"老虎/纳米机器/那个 bug"）DF 低 → IDF 高 → 强信号

两条路径共享 corpus：
- proactive: BM25 总分超 ``ANTI_REPEAT_REGEN_THRESHOLD`` → 触发 1 次 regen；
  仍超 ``ANTI_REPEAT_DROP_THRESHOLD`` → drop 本次投递
- regular reply: 只把 top-K BM25 ngram 注入下次会话 system prompt 提示模型
  "最近你已经聊过 X / Y / Z"，不做硬拦

设计要点
--------
- **存储**：``memory/{name}/anti_repeat_corpus.json``。schema 见 ``_default_payload``
- **滚动**：append 时若超 ``BG_WINDOW`` 弹出最老的；DF 不维护反向索引，每
  次查询线性扫 BG 一遍（N=100 量级，性能无关）
- **token 化**：复用 ``memory.persona._extract_keywords``（CJK 2/3-gram +
  拉丁分词），并去 stop names。这是项目里唯一的 keyword 抽取实现，保持单
  一事实源
- **并发**：per-character ``threading.Lock``，模式照搬 ``memory/cursors.py``
- **持久化**：每次 ``record_output`` 落盘（同 PR-1 的 user_directives 风格）

不抽取
------
- 太短的 draft（< ``ANTI_REPEAT_MIN_DRAFT_TOKENS`` ngram）：BM25 信号不稳定
  且短回复天然不会"复读"；直接 ``score=0`` 放行
- 空 corpus：BM25 退化为 0，所有 draft 都通过
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from config import (
    ANTI_REPEAT_BG_WINDOW,
    ANTI_REPEAT_BM25_B,
    ANTI_REPEAT_BM25_K1,
    ANTI_REPEAT_FG_WINDOW,
    ANTI_REPEAT_INJECT_TOP_K,
    ANTI_REPEAT_MIN_DRAFT_TOKENS,
)
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


_SCHEMA_VERSION = 1

# 空 / None 角色名归一化到这个 key。与 ``memory/user_directives.py`` 的
# sink fallback + ``main_logic/core.py`` 的 ``_directives_key`` 保持一致：
# lanlan_name 缺失时，proactive corpus 仍然要落地（否则 BM25 regen / soft
# hint 在该 session 静默失效，codex P2）。
_DEFAULT_KEY = "default"


def _resolve_name(name: Optional[str]) -> Optional[str]:
    """空 / None 角色名归一化到 ``_DEFAULT_KEY``；其它情况按原样返回。"""
    if not name:
        return _DEFAULT_KEY
    return name


def _now() -> float:
    return time.time()


# ── ngram extraction ────────────────────────────────────────────


def _ngrams(text: str) -> List[str]:
    """对 ``text`` 抽 ngram。复用 ``memory.persona._extract_keywords`` 作为唯一
    事实源。失败回退到极简 ASCII whitespace split（不阻断主流程）。

    ``stop_names`` 用 ``collect_stop_names(config_manager)``——必填，零参数调用
    会 TypeError 让外层 except 静默吞掉、stop names 永远空，master/lanlan 名字
    渗透进 ngram 集合污染 BM25（每轮对话都出现的实体名会被算成高 DF 后压下
    IDF，间接保护了一部分，但仍把无关的 nickname 2/3-gram 灌进 corpus）。"""
    try:
        from memory.persona import _extract_keywords
        from memory.stop_names import collect_stop_names
        try:
            stop_names = collect_stop_names(get_config_manager())
        except Exception:
            stop_names = []
        return list(_extract_keywords(text or "", stop_names=stop_names))
    except Exception:
        # 兜底：persona 模块在某些 entrypoint（memory-only test）可能没加载。
        # 主路径的 ``_extract_keywords`` 返回 set（同一 doc 内 ngram 去重），下游
        # bm25_score 的 ``doc.count(term)`` 因此始终是 0/1。兜底也必须维持同款
        # "每 doc 至多 1 次" 语义，否则 ``bug bug bug`` 在 fallback 入口下被算
        # 成 TF=3，BM25 阈值会比主路径敏感得多——同一段文本走两条路径分数差几倍。
        return list({t for t in (text or "").split() if len(t) >= 2})


# ── 持久化 schema ────────────────────────────────────────────────


def _default_payload() -> Dict[str, Any]:
    return {"version": _SCHEMA_VERSION, "window": []}


def _normalize_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """读盘条目归一化。失败 → None。

    Entry shape: ``{"ts": float, "ngrams": [str], "is_proactive": bool}``
    """
    if not isinstance(raw, dict):
        return None
    try:
        ngrams = raw.get("ngrams") or []
        if not isinstance(ngrams, list):
            return None
        # 强制 list[str]，丢掉非 str 元素
        clean = [s for s in ngrams if isinstance(s, str) and s]
        if not clean:
            return None
        return {
            "ts": float(raw.get("ts") or 0) or _now(),
            "ngrams": clean,
            "is_proactive": bool(raw.get("is_proactive", False)),
        }
    except Exception:
        return None


# ── BM25 scoring ────────────────────────────────────────────────


def bm25_score(
    draft_ngrams: List[str],
    fg_docs: List[List[str]],
    bg_docs: Optional[List[List[str]]] = None,
    *,
    k1: float = ANTI_REPEAT_BM25_K1,
    b: float = ANTI_REPEAT_BM25_B,
) -> Tuple[float, Dict[str, float]]:
    """计算 ``draft`` 在前景窗 ``fg_docs`` 上的"重复度" BM25 分。

    与经典搜索向 BM25 的关键差异：经典 BM25 把 "rare in corpus" 算高分（搜索
    相关性偏好罕见关键词），但**复读检测**要的是 "在背景里罕见 + 最近频繁
    出现"——前者由 BG 大窗算 IDF，后者由 FG 小窗算 TF 累加。所以：

    - ``bg_docs`` (默认 = fg_docs) 算 DF/IDF：term 在多少条**全窗**里出现过
    - ``fg_docs`` 算 TF：term 在**最近 FG 条**里累计 frequency
    - 总分 = Σ_term IDF_bg(term) × Σ_doc∈fg BM25_tf_norm(term, doc)

    举例：
    - "老虎" 只在最近 5 条 FG 全出现（5/5），但在 100 条 BG 里只占 5/100 →
      IDF_bg 高 + TF 高 → 重复度大；触发 regen
    - "今天" 在 BG 100 条几乎全出现 → IDF_bg 接近 0 → 公共词不打分
    - 1 条偶发 unique 词在 FG 只出现 1 次 → TF 累加小 → 单次出现不至于触发

    返回 ``(total, per_term)``。``per_term`` 只含有正贡献，按分排序。

    边界：
    - 空 ``fg_docs`` 或空 ``draft_ngrams`` → ``(0.0, {})``
    """
    if not draft_ngrams or not fg_docs:
        return 0.0, {}
    if bg_docs is None:
        bg_docs = fg_docs

    n_bg = len(bg_docs) or 1
    avgdl = sum(len(d) for d in fg_docs) / len(fg_docs) if fg_docs else 0.0
    if avgdl <= 0:
        return 0.0, {}

    # DF 在 BG 窗上算；用 set 避免一条文档里同 ngram 重复
    df: Dict[str, int] = {}
    for doc in bg_docs:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1

    draft_unique = set(draft_ngrams)

    total = 0.0
    per_term_total: Dict[str, float] = {}
    for term in draft_unique:
        n = df.get(term, 0)
        # IDF Robertson-Sparck-Jones (+0.5 平滑)。term 没在 BG 里出现也按
        # 0 处理（视作完全 unique，避免对 BG 缺失项倾斜过高的 IDF）。
        if n <= 0:
            continue
        idf = math.log((n_bg - n + 0.5) / (n + 0.5) + 1.0)
        if idf <= 0:
            continue
        term_score = 0.0
        for doc in fg_docs:
            tf = doc.count(term)
            if tf == 0:
                continue
            dl = len(doc) or 1
            norm = 1 - b + b * dl / avgdl
            term_score += idf * (tf * (k1 + 1)) / (tf + k1 * norm)
        if term_score > 0:
            per_term_total[term] = term_score
            total += term_score
    return total, dict(
        sorted(per_term_total.items(), key=lambda kv: kv[1], reverse=True)
    )


# ── manager ─────────────────────────────────────────────────────


class AntiRepeatCorpus:
    """Per-character 滚动 corpus（线程安全）。

    用法：
        store = AntiRepeatCorpus()
        store.record_output(name, ai_text, is_proactive=True)
        total, terms = store.score_draft(name, draft_text)
        if total > REGEN_THRESHOLD: ... regen ...
        hint_terms = store.top_recent_topics(name, k=6)
    """

    def __init__(self) -> None:
        self._config_manager = get_config_manager()
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ── path / lock ────────────────────────────────────────

    def _file_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            "anti_repeat_corpus.json",
        )

    def _get_lock(self, name: str) -> threading.Lock:
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:
                    self._locks[name] = threading.Lock()
        return self._locks[name]

    # ── load / save (锁由调用方持有) ───────────────────────

    def _load_unlocked(self, name: str) -> List[Dict[str, Any]]:
        if name in self._cache:
            return self._cache[name]
        window: List[Dict[str, Any]] = []
        path = self._file_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                items = raw.get("window") if isinstance(raw, dict) else None
                if isinstance(items, list):
                    for r in items:
                        norm = _normalize_entry(r)
                        if norm is not None:
                            window.append(norm)
            except Exception as exc:
                logger.warning(
                    "[AntiRepeat] load failed for %s, starting empty: %s",
                    name, exc,
                )
                window = []
        # 立刻按当前 BG_WINDOW 裁掉过老条目——磁盘上的文件可能是旧配置下写的
        # （ANTI_REPEAT_BG_WINDOW 后续调低过），或者 record_output 写入时
        # 中途被 crash 切断没来得及裁。否则首次 score_draft / top_recent_topics
        # 会吃到过期历史拉偏 BM25。
        if len(window) > ANTI_REPEAT_BG_WINDOW:
            window.sort(key=lambda e: float(e.get("ts", 0)))
            window = window[-ANTI_REPEAT_BG_WINDOW:]
        self._cache[name] = window
        return window

    def _save_unlocked(self, name: str) -> None:
        path = self._file_path(name)
        payload = {
            "version": _SCHEMA_VERSION,
            "window": self._cache.get(name, []),
        }
        try:
            atomic_write_json(path, payload, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.warning("[AntiRepeat] save failed for %s: %s", name, exc)

    # ── public API ─────────────────────────────────────────

    def record_output(
        self,
        name: str,
        text: str,
        *,
        is_proactive: bool = False,
        now: Optional[float] = None,
    ) -> None:
        """登记一条 AI 输出（既写背景 corpus 也参与下次评分）。

        - 太短的 text（ngram < ``ANTI_REPEAT_MIN_DRAFT_TOKENS``）不入库——免得
          把"嗯"、"好"这类发言摊薄 DF
        - 入库后若窗口长度 > ``ANTI_REPEAT_BG_WINDOW`` 弹出最老
        - 空 name 归一化到 ``_DEFAULT_KEY``（与 user_directives sink / 注入路径
          一致），否则空 lanlan_name 配置下 BM25 / soft hint 整段失效（codex P2）
        """
        if not text or not text.strip():
            return
        name = _resolve_name(name)
        ngrams = _ngrams(text)
        if len(ngrams) < ANTI_REPEAT_MIN_DRAFT_TOKENS:
            return
        ts = float(now if now is not None else _now())
        entry = {
            "ts": ts,
            "ngrams": ngrams,
            "is_proactive": bool(is_proactive),
        }
        with self._get_lock(name):
            window = self._load_unlocked(name)
            window.append(entry)
            # 滚动：超 BG_WINDOW 弹最老（按 ts 排序保险——理论上 append 时序就单调）
            if len(window) > ANTI_REPEAT_BG_WINDOW:
                window.sort(key=lambda e: float(e.get("ts", 0)))
                del window[: len(window) - ANTI_REPEAT_BG_WINDOW]
            self._cache[name] = window
            self._save_unlocked(name)

    def score_draft(
        self,
        name: str,
        draft_text: str,
        *,
        fg_window: int = ANTI_REPEAT_FG_WINDOW,
    ) -> Tuple[float, Dict[str, float]]:
        """对 draft 做 BM25 评分（vs 最近 ``fg_window`` 条 AI 输出）。

        返回 ``(total_score, per_term_score)``。
        - 太短的 draft / 空 corpus → ``(0.0, {})``
        - 不读 BG corpus 的"前 N - fg"段——那部分只贡献 DF，不直接参与评分；
          但 DF 仍然是基于 BG 整窗算的，让"长期未出现"的 unique 词得到更高 IDF
        - 空 name → 归一化到 ``_DEFAULT_KEY``（与 record_output 对齐）
        """
        if not draft_text or not draft_text.strip():
            return 0.0, {}
        name = _resolve_name(name)
        draft_ngrams = _ngrams(draft_text)
        if len(draft_ngrams) < ANTI_REPEAT_MIN_DRAFT_TOKENS:
            return 0.0, {}
        with self._get_lock(name):
            window = self._load_unlocked(name)
            # 前景：靠后那段；背景：整个 BG 窗（含 FG）
            if fg_window > 0 and len(window) > fg_window:
                fg_docs = [e["ngrams"] for e in window[-fg_window:]]
            else:
                fg_docs = [e["ngrams"] for e in window]
            bg_docs = [e["ngrams"] for e in window]
        if not fg_docs:
            return 0.0, {}
        return bm25_score(draft_ngrams, fg_docs, bg_docs)

    def top_recent_topics(
        self,
        name: str,
        *,
        k: int = ANTI_REPEAT_INJECT_TOP_K,
        fg_window: int = ANTI_REPEAT_FG_WINDOW,
    ) -> List[str]:
        """返回最近 fg_window 条里 BM25-rank 最高的 K 个 ngram。

        用法：注入下一轮 system prompt 提示模型"最近聊过 X / Y / Z"。
        DF 用整个 BG 窗算（让经常出现的高频词 IDF 低），TF 用 FG 窗：
        效果是"最近 5 条里频繁出现 + 整体 corpus 里不常见"的 ngram 排前面。

        实现：把 FG 窗自己当 draft 求 BM25 自评分。

        空 name 归一化到 ``_DEFAULT_KEY`` 与 record_output / score_draft 对齐。
        """
        if k <= 0:
            return []
        name = _resolve_name(name)
        with self._get_lock(name):
            window = self._load_unlocked(name)
            if not window:
                return []
            if fg_window > 0 and len(window) > fg_window:
                fg_docs = [e["ngrams"] for e in window[-fg_window:]]
            else:
                fg_docs = [e["ngrams"] for e in window]
            bg_docs = [e["ngrams"] for e in window]
        # 把 fg 窗里所有 ngram 拼成一个"伪 draft"
        synthetic_draft: List[str] = []
        for doc in fg_docs:
            synthetic_draft.extend(doc)
        if not synthetic_draft:
            return []
        _total, per_term = bm25_score(synthetic_draft, fg_docs, bg_docs)
        return list(per_term.keys())[:k]

    def clear(self, name: str) -> None:
        name = _resolve_name(name)
        with self._get_lock(name):
            self._cache[name] = []
            self._save_unlocked(name)


# ── 进程级单例 ─────────────────────────────────────────────
_GLOBAL_CORPUS: Optional[AntiRepeatCorpus] = None
_GLOBAL_LOCK = threading.Lock()


def get_anti_repeat_corpus() -> AntiRepeatCorpus:
    global _GLOBAL_CORPUS
    if _GLOBAL_CORPUS is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_CORPUS is None:
                _GLOBAL_CORPUS = AntiRepeatCorpus()
    return _GLOBAL_CORPUS
