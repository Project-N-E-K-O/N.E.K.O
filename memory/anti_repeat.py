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
AntiRepeatCorpus — per-character rolling corpus + BM25 scorer for automatic
anti-repetition of AI output (unrelated to user behavior).

Motivation
--------
When generating proactive chats back-to-back, the LLM tends to circle back to the
same topic ("the tiger shows up again", "let's talk about ... again"). Simple
SequenceMatcher similarity only catches "exact repeats" and is useless against
"rephrased but still on the same topic".

We use BM25:
- background corpus = the most recent ``ANTI_REPEAT_BG_WINDOW`` AI outputs, each
  stored as an ngram set (count-capped only — never time-filtered, so IDF context
  survives idle periods intact)
- foreground query = the most recent ``ANTI_REPEAT_FG_WINDOW`` entries (a subset of
  the background, the trailing slice) that are ALSO within
  ``ANTI_REPEAT_FG_TTL_SECONDS``. TF/repetition is a recency signal, so a window
  left idle past the TTL empties and scoring returns 0 — this is what stops the
  proactive "idle deadlock" (frozen FG re-scoring the same high value every cycle
  because drop paths never advance the corpus)
- new draft score = Σ BM25(term, fg) over the draft's ngrams
- key property: frequent common words ("今天/觉得/哈哈/嗯") have high DF → low IDF →
  contribute almost nothing; topic words ("老虎/纳米机器/那个 bug") have low DF →
  high IDF → strong signal

Two paths share the corpus:
- proactive: total BM25 above ``ANTI_REPEAT_REGEN_THRESHOLD`` → trigger 1 regen;
  still above ``ANTI_REPEAT_DROP_THRESHOLD`` → drop this delivery
- regular reply: only inject the top-K BM25 ngrams into the next session's system
  prompt to tell the model "you've recently talked about X / Y / Z"; no hard block

Design notes
--------
- **Storage**: ``memory/{name}/anti_repeat_corpus.json``. Schema: see ``_default_payload``
- **Rolling**: on append, pop the oldest once over ``BG_WINDOW``; DF keeps no
  inverted index — every query linearly scans the BG once (N=100 scale,
  performance irrelevant)
- **Tokenization**: reuses ``memory.persona._extract_keywords`` (CJK 2/3-grams +
  Latin word split) and strips stop names. This is the project's only keyword
  extraction implementation; keep the single source of truth
- **Concurrency**: per-character ``threading.Lock``, pattern copied from ``memory/cursors.py``
- **Persistence**: every ``record_output`` writes to disk (same style as PR-1's user_directives)

Not extracted
------
- Too-short drafts (< ``ANTI_REPEAT_MIN_DRAFT_TOKENS`` ngrams): the BM25 signal is
  unstable there, and short replies don't naturally "repeat"; pass with ``score=0``
- Empty corpus: BM25 degrades to 0; every draft passes
"""  # noqa: DOCSTRING_CJK
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
    ANTI_REPEAT_FG_TTL_SECONDS,
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
    """Normalize empty / None character names to ``_DEFAULT_KEY``; anything else is returned as-is."""
    if not name:
        return _DEFAULT_KEY
    return name


def _now() -> float:
    return time.time()


# ── ngram extraction ────────────────────────────────────────────


def _ngrams(text: str) -> List[str]:
    """Extract ngrams from ``text``. Reuses ``memory.persona._extract_keywords`` as the
    single source of truth. On failure, falls back to a minimal ASCII whitespace
    split (never blocking the main flow).

    ``stop_names`` uses ``collect_stop_names(config_manager)`` — required; a
    zero-argument call would TypeError, get silently swallowed by the outer except,
    and stop names would stay empty forever, letting master/lanlan names seep into
    the ngram set and pollute BM25 (entity names appearing every turn get a high DF
    that suppresses their IDF, which indirectly protects part of it, but irrelevant
    nickname 2/3-grams would still flood the corpus)."""
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
    """Normalize an entry read from disk. Failure → None.

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
    """Compute the "repetitiveness" BM25 score of ``draft`` over the foreground window ``fg_docs``.

    Key difference from classic search-oriented BM25: classic BM25 scores "rare in
    corpus" high (search relevance prefers rare keywords), but **repetition
    detection** wants "rare in the background + frequent recently" — the former
    comes from IDF over the large BG window, the latter from accumulated TF over
    the small FG window. So:

    - ``bg_docs`` (default = fg_docs) computes DF/IDF: how many docs of the
      **full window** the term appears in
    - ``fg_docs`` computes TF: the term's cumulative frequency over the **most
      recent FG entries**
    - total = Σ_term IDF_bg(term) × Σ_doc∈fg BM25_tf_norm(term, doc)

    Examples:
    - "老虎" appears in all of the last 5 FG entries (5/5) but only in 5/100 of the
      BG → high IDF_bg + high TF → high repetitiveness; triggers regen
    - "今天" appears in nearly all 100 BG entries → IDF_bg near 0 → common words
      don't score
    - a stray unique term appearing once in FG → small TF accumulation → a single
      occurrence won't trigger

    Returns ``(total, per_term)``. ``per_term`` only contains positive
    contributions, sorted by score.

    Edge cases:
    - empty ``fg_docs`` or empty ``draft_ngrams`` → ``(0.0, {})``
    """  # noqa: DOCSTRING_CJK
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
    """Per-character rolling corpus (thread-safe).

    Usage:
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
        """Register one AI output (written into the background corpus and used in later scoring).

        - Too-short text (ngrams < ``ANTI_REPEAT_MIN_DRAFT_TOKENS``) is not stored —
          keeps utterances like "嗯" / "好" from diluting DF
        - After insertion, pop the oldest once the window exceeds ``ANTI_REPEAT_BG_WINDOW``
        - Empty names normalize to ``_DEFAULT_KEY`` (consistent with the
          user_directives sink / injection path); otherwise BM25 / soft hints would
          break entirely under an empty lanlan_name config (codex P2)
        """  # noqa: DOCSTRING_CJK
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

    @staticmethod
    def _split_fg_bg(
        window: List[Dict[str, Any]],
        fg_window: int,
        now: Optional[float],
    ) -> Tuple[List[List[str]], List[List[str]]]:
        """Build ``(fg_docs, bg_docs)`` from a loaded window.

        - **BG** = the entire count-capped window, unfiltered → DF/IDF (word
          frequency background). Never time-filtered, so IDF context is preserved
          in full regardless of idle time.
        - **FG** = the trailing ``fg_window`` entries, but ONLY those staged within
          ``ANTI_REPEAT_FG_TTL_SECONDS`` of ``now``. TF / repetition is a recency
          signal: stale entries fall out so an idle-frozen window stops scoring.

        This is the deadlock fix: proactive fires only while the user is idle, and
        every drop path skips ``record_output`` (only a successful delivery / a real
        user reply appends), so during idle the trailing FG stays frozen on the last
        few same-topic lines and re-scores identically every cycle → permanent drop.
        Aging the FG out (BG untouched) makes ``bm25_score`` return 0 once nothing
        recent remains, which is also semantically correct — a topic last touched
        >TTL ago is not "back-to-back repetition".
        """
        bg_docs = [e["ngrams"] for e in window]
        ref = float(now if now is not None else _now())
        fresh = [
            e for e in window
            if ref - float(e.get("ts", 0.0)) <= ANTI_REPEAT_FG_TTL_SECONDS
        ]
        if fg_window > 0 and len(fresh) > fg_window:
            fg_docs = [e["ngrams"] for e in fresh[-fg_window:]]
        else:
            fg_docs = [e["ngrams"] for e in fresh]
        return fg_docs, bg_docs

    def score_draft(
        self,
        name: str,
        draft_text: str,
        *,
        fg_window: int = ANTI_REPEAT_FG_WINDOW,
        now: Optional[float] = None,
    ) -> Tuple[float, Dict[str, float]]:
        """BM25-score a draft (vs the most recent ``fg_window`` AI outputs).

        Returns ``(total_score, per_term_score)``.
        - Too-short draft / empty corpus → ``(0.0, {})``
        - The "first N - fg" slice of the BG corpus is not read — it only contributes
          DF and doesn't directly participate in scoring; but DF is still computed
          over the whole BG window, giving "long-unseen" unique terms a higher IDF
        - FG only counts entries within ``ANTI_REPEAT_FG_TTL_SECONDS`` (see
          ``_split_fg_bg``); when the whole trailing window has aged out (idle) the
          score falls to 0 and the draft passes — the anti-repeat deadlock fix
        - ``now`` overrides the clock (tests); production passes None → wall clock
        - Empty name → normalized to ``_DEFAULT_KEY`` (aligned with record_output)
        """
        if not draft_text or not draft_text.strip():
            return 0.0, {}
        name = _resolve_name(name)
        draft_ngrams = _ngrams(draft_text)
        if len(draft_ngrams) < ANTI_REPEAT_MIN_DRAFT_TOKENS:
            return 0.0, {}
        with self._get_lock(name):
            window = self._load_unlocked(name)
            fg_docs, bg_docs = self._split_fg_bg(window, fg_window, now)
        if not fg_docs:
            return 0.0, {}
        return bm25_score(draft_ngrams, fg_docs, bg_docs)

    def top_recent_topics(
        self,
        name: str,
        *,
        k: int = ANTI_REPEAT_INJECT_TOP_K,
        fg_window: int = ANTI_REPEAT_FG_WINDOW,
        now: Optional[float] = None,
    ) -> List[str]:
        """Return the K highest BM25-ranked ngrams within the most recent fg_window entries.

        Usage: inject into the next round's system prompt to tell the model "you've
        recently talked about X / Y / Z".
        DF uses the whole BG window (frequently appearing common words get low IDF),
        TF uses the FG window: the effect is that ngrams "frequent in the last 5
        entries + uncommon in the overall corpus" rank first.

        Implementation: treat the FG window itself as a draft and compute its BM25
        self-score.

        FG honors ``ANTI_REPEAT_FG_TTL_SECONDS`` (see ``_split_fg_bg``): when the
        recent window has aged out there are no "recent topics" to warn about, so an
        empty list is returned. Empty names normalize to ``_DEFAULT_KEY``, aligned
        with record_output / score_draft.
        """
        if k <= 0:
            return []
        name = _resolve_name(name)
        with self._get_lock(name):
            window = self._load_unlocked(name)
            if not window:
                return []
            fg_docs, bg_docs = self._split_fg_bg(window, fg_window, now)
        if not fg_docs:
            return []
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
