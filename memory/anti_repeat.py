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
- Too-short regular drafts (< ``ANTI_REPEAT_MIN_DRAFT_TOKENS`` ngrams): the BM25
  signal is unstable there, and short replies don't naturally "repeat"; pass
  with ``score=0``. The separate unanswered-proactive scorer uses its lower
  proactive-only threshold so concise reminders remain detectable.
- Empty corpus: BM25 degrades to 0; every draft passes
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import asyncio
import json
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from config import (
    ANTI_REPEAT_BG_WINDOW,
    ANTI_REPEAT_BM25_B,
    ANTI_REPEAT_BM25_K1,
    ANTI_REPEAT_FG_TTL_SECONDS,
    ANTI_REPEAT_FG_WINDOW,
    ANTI_REPEAT_INJECT_TOP_K,
    ANTI_REPEAT_MIN_DRAFT_TOKENS,
    ANTI_REPEAT_UNANSWERED_MAX_AGE_SECONDS,
    ANTI_REPEAT_UNANSWERED_MIN_DRAFT_TOKENS,
    ANTI_REPEAT_UNANSWERED_MIN_MATCHES,
    ANTI_REPEAT_UNANSWERED_SIMILARITY_THRESHOLD,
    ANTI_REPEAT_UNANSWERED_WINDOW,
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


@dataclass(frozen=True, slots=True)
class UnansweredProactiveRepeatSignal:
    """Long-window evidence that the user keeps ignoring the same content shape."""

    triggered: bool = False
    match_count: int = 0
    considered_count: int = 0
    best_similarity: float = 0.0
    repeated_terms: tuple[str, ...] = ()


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
        # 落盘用的第二把锁，和数据锁分开 —— 见 _flush_snapshot 的注释。
        self._write_locks: Dict[str, threading.Lock] = {}
        self._staged_seq: Dict[str, int] = {}
        self._written_seq: Dict[str, int] = {}
        # 已摘下的落盘 task。只被局部变量引用的 task 会被 GC 回收，事件循环不保证
        # 跑完它 —— 必须由这里持强引用到完成为止。见 flush_staged_detached。
        self._detached_flushes: set = set()

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

    def _get_write_lock(self, name: str) -> threading.Lock:
        if name not in self._write_locks:
            with self._locks_guard:
                if name not in self._write_locks:
                    self._write_locks[name] = threading.Lock()
        return self._write_locks[name]

    def _stage_snapshot_unlocked(self, name: str) -> Tuple[Dict[str, Any], int]:
        """Take a numbered copy of the in-memory window. Caller holds the data lock."""
        seq = self._staged_seq.get(name, 0) + 1
        self._staged_seq[name] = seq
        payload = {
            "version": _SCHEMA_VERSION,
            "window": list(self._cache.get(name, [])),
        }
        return payload, seq

    def _flush_snapshot(self, name: str, payload: Dict[str, Any], seq: int) -> None:
        """Write a staged snapshot to disk **without** holding the data lock.

        The data lock must not be held across the write. ``arecord_output``
        runs the whole record on a worker thread, while the scoring paths
        (``score_draft`` / ``score_unanswered_proactive_draft`` /
        ``top_recent_topics``) still take that lock synchronously on the event
        loop. Holding it across ``atomic_write_json`` — whose tail is an
        unbounded fsync — would make those readers block the loop waiting on a
        worker, which is the exact stall this off-loading exists to remove.
        Same shape as the RLock transitivity fixed in
        ``main_routers/system_router/prompt_flows.py``.

        Writers instead serialize on a second, writer-only lock. Ordering is
        settled by the staged sequence number rather than by which worker wins
        the lock: a snapshot older than what is already on disk is dropped, so
        a late writer can never resurrect a stale window.
        """
        with self._get_write_lock(name):
            if seq <= self._written_seq.get(name, 0):
                return
            try:
                atomic_write_json(
                    self._file_path(name), payload, indent=2, ensure_ascii=False,
                )
            except Exception as exc:
                logger.warning("[AntiRepeat] save failed for %s: %s", name, exc)
                return
            self._written_seq[name] = seq

    # ── load / save (锁由调用方持有) ───────────────────────

    def _read_window_from_disk(self, name: str) -> List[Dict[str, Any]]:
        """Read and normalize one corpus window without taking the data lock."""
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
        return window

    def _load_unlocked(self, name: str) -> List[Dict[str, Any]]:
        if name in self._cache:
            return self._cache[name]
        window = self._read_window_from_disk(name)
        self._cache[name] = window
        return window

    # ── public API ─────────────────────────────────────────

    async def apreload(self, name: str) -> None:
        """Populate the first disk-backed window before synchronous loop use.

        Scoring and staging stay synchronous because they sit on commit/order
        boundaries where adding an await would reopen cancellation races. Their
        first cache miss must therefore be paid earlier. Disk I/O happens
        without the data lock; after it completes, installation is a tiny
        in-memory critical section. ``setdefault`` preserves a window another
        caller may have populated while this read was in flight.
        """
        name = _resolve_name(name)
        with self._get_lock(name):
            if name in self._cache:
                return
        try:
            window = await asyncio.to_thread(self._read_window_from_disk, name)
        except Exception as exc:
            # Do not let a failed off-loop warmup send the same slow/unavailable
            # path lookup back through _load_unlocked on the event loop. An
            # empty cached window is the safe degraded state for this process;
            # later records still populate and persist it normally.
            logger.warning(
                "[AntiRepeat] preload failed for %s, starting empty: %s",
                name,
                exc,
            )
            window = []
        with self._get_lock(name):
            self._cache.setdefault(name, window)

    def record_output(
        self,
        name: str,
        text: str,
        *,
        is_proactive: bool = False,
        now: Optional[float] = None,
    ) -> None:
        """Register one AI output (written into the background corpus and used in later scoring).

        - Regular outputs shorter than ``ANTI_REPEAT_MIN_DRAFT_TOKENS`` are not
          stored. Proactive outputs use the lower
          ``ANTI_REPEAT_UNANSWERED_MIN_DRAFT_TOKENS`` threshold so concise
          whitespace-delimited reminders remain available to the long-window
          unanswered scorer while "嗯" / "好" still cannot dilute DF.
        - After insertion, pop the oldest once the window exceeds ``ANTI_REPEAT_BG_WINDOW``
        - Empty names normalize to ``_DEFAULT_KEY`` (consistent with the
          user_directives sink / injection path); otherwise BM25 / soft hints would
          break entirely under an empty lanlan_name config (codex P2)
        """  # noqa: DOCSTRING_CJK
        staged = self._record_in_memory(name, text, is_proactive=is_proactive, now=now)
        if staged is None:
            return
        resolved, payload, seq = staged
        # 落盘在数据锁**之外**——见 _flush_snapshot。
        self._flush_snapshot(resolved, payload, seq)

    def _record_in_memory(
        self,
        name: str,
        text: str,
        *,
        is_proactive: bool,
        now: Optional[float],
    ) -> Optional[Tuple[str, Dict[str, Any], int]]:
        """Apply one record to the in-memory window and stage it for the disk.

        Returns the resolved name plus the staged snapshot, or None when the
        text is skipped. Split out of ``record_output`` so the async twin can
        run this part inline and off-load only the write: the scoring paths
        read ``_cache``, so deferring the in-memory update to a worker would
        let the very next turn score against a corpus that is missing the
        reply just committed.
        """
        if not text or not text.strip():
            return None
        name = _resolve_name(name)
        ngrams = _ngrams(text)
        min_tokens = (
            ANTI_REPEAT_UNANSWERED_MIN_DRAFT_TOKENS
            if is_proactive
            else ANTI_REPEAT_MIN_DRAFT_TOKENS
        )
        if len(ngrams) < min_tokens:
            return None
        ts = float(now if now is not None else _now())
        entry = {
            "ts": ts,
            "ngrams": ngrams,
            "is_proactive": bool(is_proactive),
        }
        with self._get_lock(name):
            window = self._load_unlocked(name)
            window.append(entry)
            # 每次都按 ts 排序，不再只在超窗时排。原来「append 时序天然单调」的假设
            # 靠的是调用方串行；打分侧是拿尾部切片当「最近几条」的（_split_fg_bg），
            # 错序会让旧回复被当成更新的。窗口只有 ~100 条，每次排一遍可以忽略。
            window.sort(key=lambda e: float(e.get("ts", 0)))
            if len(window) > ANTI_REPEAT_BG_WINDOW:
                del window[: len(window) - ANTI_REPEAT_BG_WINDOW]
            self._cache[name] = window
            payload, seq = self._stage_snapshot_unlocked(name)
        return name, payload, seq

    def stage_output(
        self,
        name: str,
        text: str,
        *,
        is_proactive: bool = False,
        now: Optional[float] = None,
    ) -> Optional[Tuple[str, Dict[str, Any], int]]:
        """Apply one record in memory now; return a handle to flush later.

        For callers that must satisfy two conflicting orderings at once. The
        in-memory half has to land BEFORE the turn's terminal signals: the
        client can send its next message the instant it sees turn-end, and
        scoring that message against a corpus still missing the reply just
        committed is how the same line gets said twice. The disk half has to
        land AFTER them: it is an ``await``, and a cancellation there would
        otherwise skip the terminal signals entirely, leaving a visible turn
        with no completion.

        Splitting the two satisfies both — this call takes no await, so it
        cannot be a cancellation point. Returns None when the text is skipped.
        """
        return self._record_in_memory(name, text, is_proactive=is_proactive, now=now)

    async def aflush_staged(
        self, staged: Optional[Tuple[str, Dict[str, Any], int]],
    ) -> None:
        """Write a snapshot staged by ``stage_output`` off the event loop."""
        if staged is None:
            return
        name, payload, seq = staged
        await asyncio.to_thread(self._flush_snapshot, name, payload, seq)

    def flush_staged_detached(
        self, staged: Optional[Tuple[str, Dict[str, Any], int]],
    ) -> None:
        """Schedule the disk half without adding a cancellation point.

        Use this wherever the caller still has to report a commit after the
        flush. ``aflush_staged`` is an ``await``, and by the time it runs the
        reply is already visible and the turn's terminal signals are already
        out — the turn has happened no matter what the caller's task does next.
        A ``CancelledError`` raised at that await is a ``BaseException``, so it
        slips past the caller's ``except Exception`` and past the ``return``
        that records the delivery. The caller's bookkeeping then reports "not
        delivered" for a turn the user watched, and the same proactive line
        becomes eligible to be sent a second time.

        Detaching keeps that stretch free of cancellation points. Ordering
        survives losing the caller: ``_flush_snapshot`` discards any snapshot
        older than what is already on disk.
        """
        if staged is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有循环可挂（同步调用方，或循环已经关停）。落盘是 best-effort，放弃它
            # 比在这里同步 fsync 更安全 —— 后者正是这轮改动要移出事件循环的东西。
            logger.debug("[AntiRepeat] detached flush skipped: no running loop")
            return

        task = loop.create_task(self.aflush_staged(staged))
        self._detached_flushes.add(task)

        def _done(finished: "asyncio.Task") -> None:
            self._detached_flushes.discard(finished)
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                # 摘下来之后没人 await 它了，异常不主动取一次会变成
                # "Task exception was never retrieved"。
                logger.debug("[AntiRepeat] detached flush failed: %s", exc)

        task.add_done_callback(_done)

    async def arecord_output(
        self,
        name: str,
        text: str,
        *,
        is_proactive: bool = False,
        now: Optional[float] = None,
    ) -> None:
        """Off-loop twin of ``record_output`` for callers inside a coroutine.

        ``record_output`` ends in an ``atomic_write_json``, which runs mkdir,
        a stale-temp directory scan, mkstemp, write and an unbounded
        ``os.fsync`` on the calling thread. This corpus is written on EVERY
        committed assistant reply, so on the realtime session's loop that
        physical flush lands between audio chunks.

        Callers that run this on an event loop must preload the first disk read
        with ``apreload``. The in-memory update remains on the caller so the
        next turn sees it immediately; only persistence is off-loaded.
        """
        # 内存更新留在调用线程上，只有落盘去 worker。整次记录都丢进 worker 的话，
        # 在那个 job 排队 / 算 ngram 的这段时间里，事件循环上的 score_draft /
        # top_recent_topics 会读到还没加进这条回复的旧 _cache —— 紧接着的下一轮
        # 就可能把刚说过的话又说一遍。数据锁此刻只覆盖几微秒的内存操作（落盘已经
        # 挪出去了，见 _flush_snapshot），所以在循环上取它是安全的。
        stamped = float(now if now is not None else _now())
        staged = self._record_in_memory(
            name, text, is_proactive=is_proactive, now=stamped,
        )
        if staged is None:
            return
        resolved, payload, seq = staged
        await asyncio.to_thread(self._flush_snapshot, resolved, payload, seq)

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

    def score_unanswered_proactive_draft(
        self,
        name: str,
        draft_text: str,
        *,
        silence_since: Optional[float],
        now: Optional[float] = None,
        max_age_seconds: float = ANTI_REPEAT_UNANSWERED_MAX_AGE_SECONDS,
        window: int = ANTI_REPEAT_UNANSWERED_WINDOW,
        similarity_threshold: float = ANTI_REPEAT_UNANSWERED_SIMILARITY_THRESHOLD,
        min_matches: int = ANTI_REPEAT_UNANSWERED_MIN_MATCHES,
    ) -> UnansweredProactiveRepeatSignal:
        """Detect recurring proactive content while the user remains silent.

        This is deliberately separate from the short-lived BM25 foreground:
        BM25 prevents back-to-back topic repetition, while this signal catches a
        recurring template that reappears every few turns or hours. Only
        proactive outputs delivered after ``silence_since`` participate, so any
        genuine user message resets the evidence without mutating the corpus.
        """
        if (
            silence_since is None
            or not draft_text
            or not draft_text.strip()
            or window <= 0
            or min_matches <= 0
        ):
            return UnansweredProactiveRepeatSignal()

        name = _resolve_name(name)
        draft_ngrams = set(_ngrams(draft_text))
        if len(draft_ngrams) < ANTI_REPEAT_UNANSWERED_MIN_DRAFT_TOKENS:
            return UnansweredProactiveRepeatSignal()

        ref = float(now if now is not None else _now())
        lower_bound = max(float(silence_since), ref - max(0.0, max_age_seconds))
        with self._get_lock(name):
            corpus = self._load_unlocked(name)
            candidates = [
                entry
                for entry in corpus
                if entry.get("is_proactive")
                and lower_bound < float(entry.get("ts", 0.0)) <= ref
            ][-window:]

        matches: list[tuple[float, set[str]]] = []
        for entry in candidates:
            old_ngrams = set(entry.get("ngrams") or ())
            if not old_ngrams:
                continue
            overlap = draft_ngrams & old_ngrams
            similarity = (2.0 * len(overlap)) / (
                len(draft_ngrams) + len(old_ngrams)
            )
            if similarity >= similarity_threshold:
                matches.append((similarity, old_ngrams))

        if not matches:
            return UnansweredProactiveRepeatSignal(
                considered_count=len(candidates),
            )

        term_frequency: dict[str, int] = {}
        for _similarity, old_ngrams in matches:
            for term in draft_ngrams & old_ngrams:
                term_frequency[term] = term_frequency.get(term, 0) + 1
        repeated_terms = tuple(
            term
            for term, _count in sorted(
                term_frequency.items(),
                key=lambda item: (-item[1], -len(item[0]), item[0]),
            )[:ANTI_REPEAT_INJECT_TOP_K]
        )
        match_count = len(matches)
        return UnansweredProactiveRepeatSignal(
            triggered=match_count >= min_matches,
            match_count=match_count,
            considered_count=len(candidates),
            best_similarity=max(similarity for similarity, _ in matches),
            repeated_terms=repeated_terms,
        )

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
            payload, seq = self._stage_snapshot_unlocked(name)
        # 与 record_output 同款：落盘不许在数据锁里做，否则事件循环上的打分调用
        # 会卡在这次 fsync 上。清空也走 seq，免得它被一次在飞的旧快照写盖回去。
        self._flush_snapshot(name, payload, seq)


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
