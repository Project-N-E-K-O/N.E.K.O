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
Hybrid memory recall — BM25 + cosine embedding parallel retrieval with
Reciprocal Rank Fusion. The user-facing backend for the ``recall_memory``
tool that ``main_logic/core.py`` calls when the model emits a tool call.

Pool composition
================
- **BM25 pool**:      facts (active) + reflections (active) + facts_archive
  BM25 is cheap on small corpora; including archive lets the model surface
  long-tail keyword hits that have aged out of the live working set.
  Archived rows whose id is still active are dropped first — an interrupted
  archive commit leaves the row in both files (see ``_drop_archive_overlap``).

- **Embedding pool**: facts (active) + reflections (active)
  Excludes archive (cost + recency window) and *persona* (already rendered
  into system prompt every turn — re-surfacing it via recall is redundant).

Pipeline
========
1. Hard filter — reuse ``MemoryRecallReranker._hard_filter`` to drop
   ``score<0`` / suppressed / terminal-status reflections / protected
   persona (last one is a no-op since persona never enters the pool, kept
   for defensive parity).
2. BM25 path — tokenize query + each doc via ``memory.persona._extract_keywords``
   (2/3-gram for CJK, whitespace split for Latin — covers zh/ja/ko/en
   uniformly without per-language tokenizers), score with standard Okapi
   BM25, threshold-filter, take top ``HYBRID_RECALL_BUDGET_EACH``.
3. Cosine path — embed query via ``EmbeddingService``, compute cosine vs
   each doc with a valid cached embedding, threshold-filter, take top
   ``HYBRID_RECALL_BUDGET_EACH``. Docs without cached embeddings are
   simply skipped (unlike ``MemoryRecallReranker`` which keeps them at
   cosine=0 to fall through to LLM rerank — we don't have an LLM stage).
4. RRF fusion — for each doc in (bm25_top ∪ cosine_top):

       RRF(d) = Σᵢ 1/(k + rank_i(d))   (k = HYBRID_RECALL_RRF_K, default 60)

   docs absent from a retriever contribute 0 for that term. Sort DESC,
   cap at ``HYBRID_RECALL_BUDGET_TOTAL``.

Why no LLM fine-rerank
======================
``MemoryRecallReranker`` (the internal Stage-2 signal-detection pipeline)
runs an 8s-timeout LLM rerank after cosine coarse rank. We deliberately
skip that here — the ``recall_memory`` tool is in the model's tool-use
loop and the human is waiting; another LLM round-trip would make the
gap user-perceptible. RRF on two ranked lists is already a strong
fusion baseline and is what production hybrid search systems (Elastic,
OpenSearch, Vespa) use as default.

Tokenizer choice
================
``_extract_keywords`` uses character-level 2/3-gram for CJK and
whitespace split for Latin. This is **NOT jieba**, which only solves
Chinese — using jieba would silently degrade Japanese / Korean recall.
The 2/3-gram approach is consistent with ``memory.anti_repeat`` and
covers all seven supported languages (zh/zh-TW/en/ja/ko/ru/es/pt)
without per-language router complexity.

The one thing character n-grams do *not* survive is zh / zh-TW, because
the two scripts share a language but almost no characters: a fact stored
as 他对机器学习很感兴趣 and the query 他對機器學習很感興趣 overlap in a
single token, and a query written in the script the fact was not stored
in scores several times lower — or drops out at ``score > 0`` entirely.
Fact text follows the *conversation* language by design, so any user who
switched input methods has both scripts sitting in ``facts.json``.
``_tokenize`` therefore folds Traditional onto Simplified on both the
query and the document side before splitting (#2584, see
``memory.script_fold``). Word choice (用户 / 使用者) is a separate axis the
fold does not address — that one is the embedding path's job.
"""  # noqa: DOCSTRING_CJK  # the zh/zh-TW examples above are the point
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import threading
import time
from collections import Counter, OrderedDict
from datetime import datetime
from typing import Any

from config import (
    HYBRID_RECALL_BM25_THRESHOLD,
    HYBRID_RECALL_BUDGET_EACH,
    HYBRID_RECALL_BUDGET_TOTAL,
    HYBRID_RECALL_COSINE_THRESHOLD,
    HYBRID_RECALL_POOL_CACHE_MAX_FILES,
    HYBRID_RECALL_RRF_K,
    HYBRID_RECALL_TIME_BUDGET,
    HYBRID_RECALL_TOKEN_CACHE_MAX_BYTES,
    HYBRID_RECALL_VEC_CACHE_MAX_BYTES,
)
from memory.evidence import evidence_score
from memory.script_fold import fold_script
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


# Okapi BM25 defaults. Don't conflate with ``ANTI_REPEAT_BM25_K1/B``,
# which are tuned for "repetition detection" (lower k1 → less TF-sensitive
# so a single high-frequency term doesn't dominate). For retrieval, the
# classical 1.5 / 0.75 is the well-trodden baseline.
_BM25_K1 = 1.5
_BM25_B = 0.75


# ── tokenization ──────────────────────────────────────────────────────


def _tokenize(text: str, stop_names: list[str] | None) -> list[str]:
    """BM25-friendly tokenize: shares the **same _SPLIT_RE + CJK n-gram + Latin
    word-split rules** as ``memory.persona._extract_keywords``, but *preserves
    multiplicity* (a list with duplicates, no dedup).

    Why not reuse ``_extract_keywords``: that one returns ``set[str]`` —
    duplicate terms within a doc get deduped → BM25's TF signal dies (\"博士\"
    appearing 5 times scores the same as once; BM25 degrades into BM1). The set
    semantics of ``_extract_keywords`` serve the ``_is_mentioned`` /
    ``anti_repeat`` use cases (\"did it appear\", not caring how many times),
    which differs from retrieval BM25's goal. So this module implements a local
    list-semantics tokenize whose splitting rules strictly mirror persona's
    (shared _SPLIT_RE + same CJK threshold + 2/3-grams + whole-Latin-run split);
    future changes to persona's tokenization must be synced here.

    Codex review #1 (commit fd2b75fc4): the earlier ``Counter`` optimization
    never took effect — with set input every key's count is 1. This version
    makes multiplicity actually reach BM25.

    stop_names: strip master/catgirl names from the text before tokenizing,
    keeping high-frequency entity names from polluting BM25 IDF.

    Traditional Chinese is folded onto Simplified first (#2584). Both the
    query and the documents come through here, so the fold is symmetric and
    never has to guess which script the user \"meant\" — it only has to make
    the two agree. It runs *before* stop-name stripping, and the stop names
    are folded with it, so a name configured in one script still strips out
    of text written in the other.
    """  # noqa: DOCSTRING_CJK
    # Lazy import: borrow _SPLIT_RE and strip_stop_names with _extract_keywords,
    # avoiding hard import-time dependency; persona may not load in certain entrypoints
    # such as memory-only tests.
    try:
        from memory.persona import _SPLIT_RE, strip_stop_names
    except Exception as exc:
        logger.warning(
            "[hybrid_recall] tokenize fallback to whitespace split: %s",
            exc,
        )
        # str() coercion handles truthy nonstrings (int/list) in malformed text,
        # as noted before codex review #1. Fold in fallback too:
        # it serves Traditional/Simplified query/doc pairs, or degradation silently regresses to #2584.
        return [t for t in fold_script(str(text or "")).split() if len(t) >= 2]

    # str() coercion matches fallback: malformed entries may have list/int
    # text, causing _SPLIT_RE.split TypeError
    # and aborting whole hybrid_recall rather than skipping one row.
    # Codex third review: normal path previously omitted coercion present only in fallback.
    # Fold before stripping, including stop_names: both enter Simplified space, allowing Traditional
    # facts' Simplified configured names (or reverse) to strip. Fold maps fixed 1:1 characters without
    # punctuation changes, preserving _SPLIT_RE split positions below.
    raw_text = fold_script(str(text or ""))
    if stop_names:
        try:
            raw_text = strip_stop_names(
                raw_text, [fold_script(str(n)) for n in stop_names],
            )
        except Exception:
            # strip_stop_names only replaces strings and should not fail; defensively
            # keep failures from breaking BM25 flow.
            pass

    out: list[str] = []
    for seg in _SPLIT_RE.split(raw_text):
        seg = seg.strip()
        if not seg:
            continue
        # Pure ASCII cannot contain CJK; handle directly as Latin (same as else below).
        # Fast path and CJK-ratio threshold must exactly match persona._extract_keywords
        # (Han + kana + Hangul = U+4E00-9FFF + U+3040-30FF + U+AC00-D7AF);
        # change both together, preventing hybrid/persona tokenization divergence.
        if seg.isascii():
            if len(seg) >= 2:
                out.append(seg)
            continue
        cjk_count = sum(
            1 for ch in seg
            if '一' <= ch <= '鿿'
            or '぀' <= ch <= 'ヿ'
            or '가' <= ch <= '힯'
        )
        if cjk_count > len(seg) // 2:
            # CJK: sliding 2-gram + 3-gram, **append without deduplication** to retain TF.
            for n in (2, 3):
                for i in range(len(seg) - n + 1):
                    out.append(seg[i:i + n])
        else:
            # Latin: whole segment is one token if len >=2; append too.
            if len(seg) >= 2:
                out.append(seg)
    return out


# ── BM25 retrieval ────────────────────────────────────────────────────


# Token cache
# ===========
# Passive turn context runs BM25 over the whole recall pool on every text
# turn, and tokenizing the pool (CJK 2/3-gram windows) was most of that cost.
# Tokens are a pure function of (stop names, text), so they are kept per text,
# stored as one ``\x00``-joined string: about a tenth of the memory of a
# token tuple, and ``split`` brings the list back at C speed.
#
# Same scan-aware admission as ``_VEC_CACHE`` (a scan never evicts what it
# touched itself), bounded by ``HYBRID_RECALL_TOKEN_CACHE_MAX_BYTES``.
# Unlike ``_VEC_CACHE`` this one takes a lock: the passive path scores in a
# worker thread while ``recall_memory`` scores on the event loop. The lock
# covers only the dict operations, never the tokenizing.
_TOKEN_SEP = "\x00"
_TOKEN_CACHE: OrderedDict[tuple[tuple[str, ...], str], tuple[str, int]] = OrderedDict()
_TOKEN_CACHE_BYTES = 0
_TOKEN_CACHE_LOCK = threading.Lock()
# Key tuple, OrderedDict node and value tuple, rounded up.
_TOKEN_ENTRY_OVERHEAD_BYTES = 256


def _doc_tokens(text, stop_names: list[str] | None, stop_key: tuple[str, ...], state: '_ScanState') -> list[str]:
    """``_tokenize(text, stop_names)``, served from ``_TOKEN_CACHE`` when it can be."""
    global _TOKEN_CACHE_BYTES

    if not isinstance(text, str) or _TOKEN_SEP in text:
        return _tokenize(text, stop_names)
    key = (stop_key, text)
    with _TOKEN_CACHE_LOCK:
        cached = _TOKEN_CACHE.get(key)
        if cached is not None:
            _TOKEN_CACHE.move_to_end(key)
            state.touched += 1
    if cached is not None:
        # _tokenize never yields an empty token, so "" can only mean no tokens.
        return cached[0].split(_TOKEN_SEP) if cached[0] else []
    tokens = _tokenize(text, stop_names)
    joined = _TOKEN_SEP.join(tokens)
    nbytes = sys.getsizeof(joined) + sys.getsizeof(text) + _TOKEN_ENTRY_OVERHEAD_BYTES
    with _TOKEN_CACHE_LOCK:
        previous = _TOKEN_CACHE.pop(key, None)
        if previous is not None:
            _TOKEN_CACHE_BYTES -= previous[1]
        while (
            _TOKEN_CACHE
            and _TOKEN_CACHE_BYTES + nbytes > HYBRID_RECALL_TOKEN_CACHE_MAX_BYTES
            and state.touched < len(_TOKEN_CACHE)
        ):
            _, evicted = _TOKEN_CACHE.popitem(last=False)
            _TOKEN_CACHE_BYTES -= evicted[1]
        if _TOKEN_CACHE_BYTES + nbytes <= HYBRID_RECALL_TOKEN_CACHE_MAX_BYTES:
            _TOKEN_CACHE[key] = (joined, nbytes)
            _TOKEN_CACHE_BYTES += nbytes
            state.touched += 1
    return tokens


def _invalidate_token_cache() -> None:
    """Drop every cached tokenization. Tests and ``_invalidate_pool_cache`` only."""
    global _TOKEN_CACHE_BYTES

    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE.clear()
        _TOKEN_CACHE_BYTES = 0


def _bm25_rank(
    query: str,
    pool: list[dict],
    *,
    stop_names: list[str] | None,
    k1: float = _BM25_K1,
    b: float = _BM25_B,
) -> list[tuple[dict, float]]:
    """Standard Okapi BM25 — score every doc in ``pool`` against ``query``.

    Returns ``[(doc, score)]`` sorted DESC. Zero-score docs are dropped
    (no term overlap). Empty query / empty pool / all-zero docs → ``[]``.
    """
    return [
        (doc, score)
        for doc, score, _tf in _bm25_scores(query, pool, stop_names=stop_names, k1=k1, b=b)
    ]


def _bm25_scores(
    query: str,
    pool: list[dict],
    *,
    stop_names: list[str] | None,
    k1: float = _BM25_K1,
    b: float = _BM25_B,
) -> list[tuple[dict, float, dict[str, int]]]:
    """``_bm25_rank`` plus each scored doc's term counts, restricted to the
    query's terms: ``set(tf)`` is ``set(_tokenize(doc)) & set(_tokenize(query))``,
    so callers that need the shared terms do not tokenize the pool again.
    """
    if not query or not pool:
        return []
    query_terms = _tokenize(query, stop_names)
    if not query_terms:
        return []

    # Tokenize all docs once (served from _TOKEN_CACHE on later calls);
    # reuse the same lists for DF and TF.
    stop_key = tuple(str(name) for name in stop_names or ())
    state = _ScanState()
    doc_terms_list: list[list[str]] = [
        _doc_tokens(d.get('text', '') or '', stop_names, stop_key, state) for d in pool
    ]

    n_docs = len(pool)
    total_len = sum(len(t) for t in doc_terms_list)
    if total_len == 0:
        return []
    avgdl = total_len / n_docs

    query_unique = set(query_terms)

    # DF + per-doc TF, **restricted to query terms** (#2550).
    #
    # Previously built full-corpus df (set(terms) per doc) plus one
    # Counter(terms) per doc, both vocabulary-sized, while scoring queries only dozens of
    # terms. For 5000 documents df has ~109k entries, <0.03% queried; same for Counter.
    # Observed combined 80ms /160ms, costlier than tokenization itself (46ms).
    #
    # Scan once and accumulate only query_unique terms; derive df from tf (term appears
    # iff tf has key), exactly matching set(terms) deduplicated counts. For 5000 documents,
    # observed 160ms →61ms. Scores bitwise unchanged: scoring still iterates ``query_unique``,
    # retaining floating-point accumulation order (iterating tf changes order and may drift final ULP,
    # theoretically breaking ties; do not incidentally optimize that).
    #
    # Most docs share no term with the query: a C-level set intersection
    # finds those and skips them before any counting. The rest are counted
    # in one C-level pass (``Counter``) and cut to the shared terms, so a
    # long query never rescans a doc once per shared term. Same counts as
    # the scan.
    df: dict[str, int] = dict.fromkeys(query_unique, 0)
    doc_tf_list: list[dict[str, int]] = []
    for terms in doc_terms_list:
        shared = query_unique.intersection(terms)
        if shared:
            counts = Counter(terms)
            tf_map: dict[str, int] = {t: counts[t] for t in shared}
        else:
            tf_map = {}
        doc_tf_list.append(tf_map)
        for t in tf_map:
            df[t] += 1

    # Robertson-Sparck-Jones IDF with +0.5 smoothing, once per query term
    # (it depends on the term alone), kept in ``query_unique`` order so the
    # per-doc sum below adds the same terms in the same order as before.
    idf_terms: list[tuple[str, float]] = []
    for q_term in query_unique:
        n = df.get(q_term, 0)
        if n <= 0:
            continue
        idf = math.log((n_docs - n + 0.5) / (n + 0.5) + 1.0)
        if idf <= 0:
            continue
        idf_terms.append((q_term, idf))

    scored: list[tuple[dict, float, dict[str, int]]] = []
    for doc, doc_terms, doc_tf in zip(pool, doc_terms_list, doc_tf_list):
        # No shared term scores 0, and 0 is dropped below anyway.
        if not doc_tf:
            continue
        dl = len(doc_terms)
        norm = 1.0 - b + b * dl / avgdl
        score = 0.0
        for q_term, idf in idf_terms:
            tf = doc_tf.get(q_term, 0)
            if tf == 0:
                continue
            score += idf * (tf * (k1 + 1)) / (tf + k1 * norm)
        if score > 0:
            scored.append((doc, score, doc_tf))

    scored.sort(key=lambda p: p[1], reverse=True)
    return scored


# ── cosine retrieval ──────────────────────────────────────────────────
#
# Decoded-vector cache
# ====================
# ``_POOL_CACHE`` (#2550) keeps the *parsed rows* resident across recalls; the
# cosine path nonetheless re-decoded every candidate's base64-fp16 embedding on
# **every query**. At 5000 rows that decode loop is ~49ms of the ~54ms cosine
# path (base64+fp16→fp32 ~32ms, per-vec ``np.isfinite`` ~7ms, sha256 ~4ms) —
# pure recomputation on a steady-state corpus where the vectors never change,
# and it all runs on the event loop, stalling every other request in the
# memory_server for its duration.
#
# So: decode once, keep the fp32 vector, and re-serve it only while the row in
# front of us is provably the row it was decoded from.
#
# The validity argument is a *delta* one, and that is what makes the hit path
# cheap. At admission time ``decode_valid_cached_embedding`` already returned
# non-None, which means all of its checks held **then**:
#
#     row['embedding_model_id'] == model_id
#     row['embedding_text_sha256'] == sha256(text)
#     decode(row['embedding']) is finite and has the model's dimension
#
# A hit therefore does not need to re-derive those facts — it needs to prove
# that none of their inputs moved since. Four comparisons do that:
#
#   1. ``model_id``            — same embedding space (dim/quantization/profile)
#   2. ``text`` **is** the cached object — identity, not equality. ``sha256`` is
#      a function of the object's bytes and ``str`` is immutable, so the same
#      object means the same hash, without hashing anything. Pool rows come from
#      ``_POOL_CACHE`` / FactStore's process cache and ``_tag_tier`` only
#      shallow-copies, so the ``text`` value object is stable across recalls;
#      a genuine reload hands us a fresh object and we simply miss.
#   3. row stamps unchanged — ``embedding_model_id`` still equals the runtime
#      model, ``embedding_text_sha256`` still equals the string we saw at
#      admission. A reloaded row can carry identical id / text / embedding
#      bytes with corrupted or missing stamps (hand edit, migration, cross-pool
#      id collision); the full validator rejects those, so a hit must too.
#   4. ``embedding`` str unchanged — the writer may have re-stamped the vector
#      itself (repair / requantize) without touching text or stamps. Compared
#      by equality; pool rows hand back the identical str object, so the
#      pointer check short-circuits the memcmp.
#
# (2)+(3) together re-establish ``stamp == sha256(text)`` from two string
# comparisons instead of a hash, which is why the steady-state path costs no
# sha256 at all — the ~4ms/query the old shape spent re-hashing 5000 rows to
# re-prove something that had not changed.
#
# Any mismatch falls through to the full ``decode_valid_cached_embedding``
# path, which applies the exact same checks plus the dimension/finite guards —
# a stale or hostile cache entry can therefore never smuggle a vector past
# validation, it can only cost one wasted probe.
#
# The row's L2 norm is cached alongside the vector for the same reason the
# vector is: it is a pure function of an unchanged vector, and recomputing it
# for the whole pool was another ~3ms/query at 5000 rows.
#
# Bounding — ``HYBRID_RECALL_VEC_CACHE_MAX_BYTES``, charged in bytes rather
# than entries because an entry also pins the row's ``embedding`` and ``text``
# strings (see the config note; entry count and resident bytes differ by ~2x).
#
# ⚠️ Admission is scan-aware, and it has to be. A plain LRU under "sequential
# full scan × pool larger than the cap" is self-destroying: each scan evicts
# the tail the previous scan just admitted, so by the time iteration reaches
# it the entry is gone — hit rate collapses to zero while every miss still
# pays admission. The guard is that a scan may never evict rows *it itself*
# touched this round: ``touched >= len(_VEC_CACHE)`` means everything resident
# belongs to this scan, and admission stops for the rest of it. A pool larger
# than the cap therefore settles on "the first N rows hit forever, the rest
# always miss" — degraded, but stable and strictly better than the bypass it
# replaces, which gave up caching for the whole pool.
#
# That guard is per-scan, so two characters alternating with a combined
# working set over the cap still evict each other and neither gets hits. What
# keeps that from being *worse* than no cache is the cost of a miss under this
# shape: one ``OrderedDict`` assignment and an integer add, no hashing, no
# extra decode. The pathological case degrades to "the cache does nothing",
# not "the cache costs more than it saves" — and it needs a corpus far past
# the point where archive sharding (#2716) is the actual answer.
#
# No lock, same reasoning as ``_POOL_CACHE``: dict ops are atomic under the
# GIL, and recall runs on the memory_server event loop.
#
# ⚠️ Same discipline as the pool cache: never hand out a stored vector that a
# caller could mutate in place. Consumers only read (``vstack`` copies), and
# this module never writes into a cached array after store.
#
# entry = (model_id, text, text_stamp, embedding, vector, norm, nbytes)
_VEC_CACHE: OrderedDict[str, tuple[str, str, Any, str, Any, float, int]] = OrderedDict()
_VEC_CACHE_BYTES = 0

# Per-entry bookkeeping that ``sys.getsizeof`` on the payload does not see:
# the 7-tuple, the OrderedDict link node, the ndarray object header. Measured
# at 351B via ``tracemalloc`` on CPython 3.11/x64 (3000 entries holding a
# 1-float vector, so the bookkeeping is what dominates); rounded up so this
# term over-estimates rather than under-estimates.
_VEC_ENTRY_OVERHEAD_BYTES = 384


class _ScanState:
    """Per-``_cosine_rank`` admission state (see the scan-aware note above)."""

    __slots__ = ('touched',)

    def __init__(self) -> None:
        self.touched = 0


def _cached_doc_vector(doc: dict, text, model_id: str, state: '_ScanState'):
    """``decode_valid_cached_embedding`` with a process-level decode cache.

    Returns ``(vector, norm)`` on validation success, else ``(None, 0.0)`` —
    verdict identical to calling ``decode_valid_cached_embedding`` directly.
    """
    global _VEC_CACHE_BYTES

    from memory.embeddings import decode_valid_cached_embedding

    did = doc.get('id')
    emb = doc.get('embedding')
    cacheable = isinstance(did, str) and bool(did) and isinstance(emb, str)

    if cacheable:
        cached = _VEC_CACHE.get(did)
        if (
            cached is not None
            and cached[0] == model_id
            # Identity comparison, not equality: same str object implies same sha256,
            # avoiding actual computation (delta argument point2 above).
            and cached[1] is text
            and doc.get('embedding_text_sha256') == cached[2]
            and doc.get('embedding_model_id') == model_id
            and cached[3] == emb
        ):
            _VEC_CACHE.move_to_end(did)
            state.touched += 1
            return cached[4], cached[5]

    # Decode once to obtain vector instead of decoding in is_cached_embedding_valid for dimension check,
    # discarding it, then decoding again via decode_embedding. Two base64 decodes + two
    # np.isfinite scans cost ~26ms of wasted work for 5000 documents (#2550).
    cvec = decode_valid_cached_embedding(doc, text, model_id)
    if cvec is None:
        return None, 0.0

    import numpy as np

    cnorm = float(np.linalg.norm(cvec))
    if not cacheable:
        return cvec, cnorm

    stamp = doc.get('embedding_text_sha256')
    nbytes = (
        int(cvec.nbytes)
        + sys.getsizeof(emb)
        + sys.getsizeof(text)
        + _VEC_ENTRY_OVERHEAD_BYTES
    )

    previous = _VEC_CACHE.pop(did, None)
    if previous is not None:
        _VEC_CACHE_BYTES -= previous[6]
    while (
        _VEC_CACHE
        and _VEC_CACHE_BYTES + nbytes > HYBRID_RECALL_VEC_CACHE_MAX_BYTES
        # Never evict entries touched by this scan itself, the mechanism of naive LRU self-destruction.
        # Hits/admissions both move_to_end; all touched entries are at tail. Until the entire cache
        # has been touched, head must be from a previous scan and is safely evictable.
        and state.touched < len(_VEC_CACHE)
    ):
        _, evicted = _VEC_CACHE.popitem(last=False)
        _VEC_CACHE_BYTES -= evicted[6]
    if _VEC_CACHE_BYTES + nbytes <= HYBRID_RECALL_VEC_CACHE_MAX_BYTES:
        _VEC_CACHE[did] = (model_id, text, stamp, emb, cvec, cnorm, nbytes)
        _VEC_CACHE_BYTES += nbytes
        state.touched += 1
    return cvec, cnorm


def _invalidate_vec_cache() -> None:
    """Drop every decoded vector. Tests and ``_invalidate_pool_cache`` only."""
    global _VEC_CACHE_BYTES

    _VEC_CACHE.clear()
    _VEC_CACHE_BYTES = 0


async def _cosine_rank(
    query: str,
    pool: list[dict],
) -> list[tuple[dict, float]]:
    """Embed query, compute cosine vs each doc with a valid cached
    embedding. Returns ``[(doc, cosine)]`` sorted DESC.

    Skips docs with no / invalid cached embedding (no fallthrough — we
    don't have an LLM rerank to bail to). Returns ``[]`` when:
    - EmbeddingService not available (model not loaded, RAM gate, etc.)
    - Empty query / empty pool
    - Query embed failed
    """
    if not query or not pool:
        return []

    from memory.embeddings import (
        get_embedding_service,
        parse_dim_from_model_id,
    )

    service = get_embedding_service()
    if not service.is_available():
        return []
    model_id = service.model_id()
    if model_id is None:
        return []

    # Wrap the entire embed + score loop in try/except so a cosine-path
    # Failures (embed_batch exception / missing numpy / bad document decode) must not discard already computed
    # BM25 results. If awaiting this task caused upstream ``hybrid_recall`` to drop BM25 on errors,
    # recall would become empty, defeating hybrid design.
    try:
        query_vectors = await service.embed_batch([query])
        if not query_vectors or query_vectors[0] is None:
            return []
        qvec = query_vectors[0]

        import numpy as np

        qarr = np.asarray(qvec, dtype=np.float32)
        qnorm = float(np.linalg.norm(qarr))
        if qnorm <= 0:
            return []

        target_dim = parse_dim_from_model_id(model_id) or int(qarr.size)

        # ── Batch scoring: decode loop collects vectors; one matrix operation computes dot products ──
        #
        # Per-row ``np.linalg.norm`` + ``np.dot`` on 5000 documents cause ~30k
        # Python↔numpy dispatches (~10µs fixed overhead each, dwarfing BLAS work).
        # Collect (doc, vec, norm) during decode, vstack into (n, dim), then
        # one matmul preserves semantics (skip cnorm<=0 and dimension mismatches).
        #
        # Norm comes with vector from ``_cached_doc_vector``; unchanged vectors necessarily have unchanged
        # norms. Recomputing whole-pool ``norm(axis=1)`` wastes ~3ms at 5000 rows. Cache misses
        # compute once during already-required decoding, where extra norm cost is negligible.
        vecs: list = []
        rows: list[dict] = []
        row_norms: list[float] = []
        state = _ScanState()
        for doc in pool:
            text = doc.get('text', '') or ''
            cvec, cnorm = _cached_doc_vector(doc, text, model_id, state)
            if cvec is None or cvec.size != target_dim:
                continue
            rows.append(doc)
            vecs.append(cvec)
            row_norms.append(cnorm)

        if not rows:
            return []

        mat = np.vstack(vecs).astype(np.float32, copy=False)
        norms = np.asarray(row_norms, dtype=np.float32)
        # Previous cnorm<=0 skipped rows; matrix version masks them too,
        # substituting divisor1.0 so zero vectors produce0 rather than NaN before keep removes them.
        keep = norms > 0
        safe_norms = np.where(keep, norms, 1.0)
        sims = (mat @ qarr) / (qnorm * safe_norms)

        scored: list[tuple[dict, float]] = [
            (doc, float(cos))
            for doc, cos, ok in zip(rows, sims, keep)
            if ok
        ]

        scored.sort(key=lambda p: p[1], reverse=True)
        return scored
    except Exception as exc:
        logger.warning(
            "[hybrid_recall] cosine path failed; falling back to BM25-only: %s: %s",
            type(exc).__name__, exc,
        )
        return []


# ── RRF fusion ────────────────────────────────────────────────────────


def _rrf_fuse(
    bm25_ranking: list[tuple[dict, float]],
    cosine_ranking: list[tuple[dict, float]],
    *,
    k: int,
    budget_total: int,
) -> list[dict]:
    """Reciprocal Rank Fusion:

        RRF(d) = Σᵢ 1 / (k + rankᵢ(d))

    where ``rankᵢ`` is doc d's 1-indexed rank in retriever i. Docs absent
    from a retriever contribute 0 for that term (equivalent to rank ∞).

    Dedup is by ``doc['id']`` — assumes all candidates carry an id, which
    is true for facts / reflections / archived facts in this codebase.
    Docs without an id are skipped (defensive; shouldn't happen).
    """
    by_id: dict[str, dict] = {}
    rrf_score: dict[str, float] = {}

    for rank, (doc, _) in enumerate(bm25_ranking, start=1):
        did = doc.get('id') or ''
        if not did:
            continue
        by_id[did] = doc
        rrf_score[did] = rrf_score.get(did, 0.0) + 1.0 / (k + rank)

    for rank, (doc, _) in enumerate(cosine_ranking, start=1):
        did = doc.get('id') or ''
        if not did:
            continue
        # Same id from both rankings → keep one doc copy; RRF accumulates.
        by_id.setdefault(did, doc)
        rrf_score[did] = rrf_score.get(did, 0.0) + 1.0 / (k + rank)

    sorted_ids = sorted(rrf_score.keys(), key=lambda i: rrf_score[i], reverse=True)
    out: list[dict] = []
    for did in sorted_ids[:budget_total]:
        d = dict(by_id[did])  # copy so we don't mutate the cached entry
        d['_rrf_score'] = rrf_score[did]
        out.append(d)
    return out


# ── pool loaders ──────────────────────────────────────────────────────
#
# Per-file parse cache (#2550)
# ============================
# ``FactStore.aload_facts`` already keeps active facts in a process-level cache
# (``FactStore._facts``), so facts.json is parsed once per process. The other two
# pools had no cache at all and were fully re-read + re-parsed on **every**
# recall. That was survivable while recall was an on-demand desktop tool call;
# group memory (#2433) turned it into a per-turn cost multiplied by group message
# rate.
#
# Invalidation is by file identity — ``(st_mtime_ns, st_size)`` — not by hooking
# the write paths. That is deliberate: the "cache + invalidate on write" design
# would require auditing every writer in memory_server (and fact_dedup, and the
# archive sweeps, and the restore paths), and a single missed writer is a silent
# stale read. Every writer here goes through ``atomic_write_json``'s
# ``os.replace``, so the replacement always carries a fresh mtime; one ``os.stat``
# per recall (microseconds) buys correctness that does not depend on knowing the
# writer set. It also picks up edits made by other processes or by hand.
#
# Order matters: stat **before** load. Stat-then-load can only ever cache data
# *newer* than the identity it is filed under (next stat mismatches → reload).
# Load-then-stat would file stale data under a fresh identity and serve it until
# the next write — the one direction that actually goes wrong.
#
# Rows are shared, not copied — every consumer on the recall path treats them as
# read-only (``_tag_tier`` shallow-copies before stamping, ``_rrf_fuse`` copies
# again before adding ``_rrf_score``). Do not hand these lists to a mutating
# caller.
#
# Eviction: LRU capped at ``HYBRID_RECALL_POOL_CACHE_MAX_FILES`` entries.
# The original version of this cache had none, on the reasoning that entries are
# "bounded by (characters × 2 files)". That bounded the entry *count* while the
# thing needing a bound is *bytes*: every character ever recalled kept its whole
# archive resident forever, so a multi-character install paid for all of them at
# once even though only one is usually active. An LRU over entries fixes exactly
# that waste — idle characters fall out — without touching the active character's
# hit rate, which is where the latency win lives. Note this deliberately does NOT
# cap a single huge archive: evicting the active character's pool would just
# restore the per-recall re-parse this cache exists to remove. Bounding total
# corpus size is a different problem, tracked separately (archive sharding).
#
# No lock: two concurrent recalls can both miss and both parse, which wastes one
# parse but cannot corrupt anything (dict assignment is atomic under the GIL).
# A lock would serialize recalls across groups for no correctness gain.

# OrderedDict, not dict: eviction needs move_to_end / popitem(last=False).
_POOL_CACHE: OrderedDict[str, tuple[tuple[int, int], list[dict]]] = OrderedDict()


def _file_identity(path) -> tuple[int, int] | None:
    """``(st_mtime_ns, st_size)`` — decides whether a cached parse is current.
    ``None`` means "no usable cache key", and the caller must then load
    uncached rather than assume the pool is empty.

    The ``isinstance(path, str)`` guard is load-bearing, not defensive noise:
    ``os.stat`` also accepts a **file descriptor**, and anything implementing
    ``__index__`` gets taken as one. A ``MagicMock`` does (so does an ``int``
    that leaked out of a path helper), which means a non-str path silently
    stats an unrelated open fd and yields a plausible-looking identity for the
    wrong file. Refusing to key the cache on anything but a real path string
    turns that into a clean cache bypass.
    """
    if not isinstance(path, str) or not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


async def _aload_pool_cached(path, aload_fn) -> list[dict]:
    """Return ``await aload_fn()``'s rows, reusing the last parse while the
    file's ``(mtime_ns, size)`` is unchanged.

    Fails **open**: when the file cannot be stat'd (missing, unreadable, or the
    path isn't a usable cache key) this calls ``aload_fn`` uncached instead of
    returning ``[]``. A caching layer that answered "no rows" on a stat failure
    would silently degrade recall quality — much worse than losing the cache
    hit, and invisible in the logs. Each loader already has its own correct
    handling for a genuinely missing file.
    """
    identity = await asyncio.to_thread(_file_identity, path)
    if identity is None:
        if isinstance(path, str):
            _POOL_CACHE.pop(path, None)
        return await aload_fn()
    cached = _POOL_CACHE.get(path)
    if cached is not None and cached[0] == identity:
        _POOL_CACHE.move_to_end(path)  # mark as most-recently used
        return cached[1]
    rows = await aload_fn()
    _POOL_CACHE[path] = (identity, rows)
    _POOL_CACHE.move_to_end(path)
    while len(_POOL_CACHE) > HYBRID_RECALL_POOL_CACHE_MAX_FILES:
        evicted, _ = _POOL_CACHE.popitem(last=False)
        logger.debug(
            "[hybrid_recall] pool cache 淘汰最久未用条目: %s", evicted,
        )
    return rows


def _invalidate_pool_cache(path: str | None = None) -> None:
    """Drop cached parses — whole cache by default, one path when given.

    Exists for tests and for callers that knowingly bypass ``atomic_write_json``;
    the normal path needs no explicit invalidation (see the module note above).
    Also drops the decoded-vector cache (``_VEC_CACHE``): its entries are
    content-validated per probe, so clearing is never *required* for
    correctness, but tests reuse doc ids across cases and a shared cache would
    turn test isolation into an accident of content addressing. The token
    cache (``_TOKEN_CACHE``) goes with it for the same reason.
    """
    _invalidate_vec_cache()
    _invalidate_token_cache()
    if path is None:
        _POOL_CACHE.clear()
    else:
        _POOL_CACHE.pop(path, None)


# Every key the recall path reads off an archived fact, and who reads it.
# Archive rows are projected onto exactly this set at load time (#2550): a real
# archived fact carries 20+ fields, recall looks at 15, and the difference is
# permanently resident once the pool cache holds it. Measured on 50k synthetic
# archived rows: 70MB → 33MB (-53%), on top of the -65% from shedding vectors.
#
#   id                                   _drop_archive_overlap, result rendering
#   text                                 _hard_filter, _bm25_rank, rendering
#   score / suppress / suppressed /
#     protected / target_type / status   MemoryRecallReranker._hard_filter
#   subject_kind / subject_id / scope    memory.scopes.filter_entries_for_subjects
#   entity                               result rendering
#   created_at / event_start_at /
#     event_end_at                       _entry_event_window, rendering
#
# Deliberately NOT carried over: embedding (archive never enters the vector
# pool), plus importance / hash / event_when_raw / schema_version / absorbed /
# signal_processed / tags / source / speaker_id — all write-path or
# reflection-synthesis concerns that no recall consumer touches.
#
# ⚠️ Adding a field read to any recall consumer means adding it here too, or it
# silently reads None on archived rows only (active facts keep every field, so
# the bug shows up exclusively for old memories — an unpleasant thing to debug).
# ``test_projected_rows_still_render_every_result_field`` runs a full recall over
# an archived row and asserts every rendered field survived, so the two cannot
# drift apart unnoticed.
_ARCHIVE_RECALL_KEYS = frozenset({
    'id', 'text',
    'score', 'suppress', 'suppressed', 'protected', 'target_type', 'status',
    'subject_kind', 'subject_id', 'scope',
    'entity',
    'created_at', 'event_start_at', 'event_end_at',
})


def _project_archive_row(row: dict) -> dict:
    """Keep only the keys recall reads. Absent keys stay absent — every consumer
    goes through ``.get()``, so "missing" and "present but None" are already
    indistinguishable to them, and the sparse form is the smaller of the two."""
    return {k: v for k, v in row.items() if k in _ARCHIVE_RECALL_KEYS}


async def _aload_archive_facts(fact_store, lanlan_name: str) -> list[dict]:
    """Load ``facts_archive.json`` directly. Returns ``[]`` on missing /
    parse error — archive miss is non-fatal for recall.

    Reaches into ``fact_store._facts_archive_path`` because there's no
    public archive loader (the FactStore archives but never re-reads its
    own archive in its hot path).

    Parses are cached by file identity (see the module note above).
    """
    try:
        path = fact_store._facts_archive_path(lanlan_name)
    except Exception as exc:
        logger.warning(
            "[hybrid_recall] %s: 无法解析 facts_archive 路径: %s",
            lanlan_name, exc,
        )
        return []

    def _read() -> list[dict]:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        # Rows archived by subject time (subject_archived_at) stay outside recall:
        # absorbed archives intentionally remain for BM25 long tail, but subject archives mean
        # this group/member's entire memory leaves candidates. This is both recall paths'
        # (hybrid_recall / recall_by_time) sole archive-pool assembly point,
        # so one filter covers both. Restoration strips marker and naturally returns rows.
        rows = [
            row for row in data
            if not (
                isinstance(row, dict)
                and (
                    row.get('subject_archived_at')
                    or row.get('arbitration_archived_at')
                )
            )
        ]
        # Project to fields recall actually reads (_ARCHIVE_RECALL_KEYS), also dropping vectors:
        # archives explicitly bypass vector pool (module-top Pool composition); vector column occupies
        # ~12/13 of file, otherwise parsed only to be ignored and retained in cache above.
        # Disk remains unchanged: restoring rows to active needs cached vectors and
        # all write-side fields, so strip only upon entering memory.
        return [
            _project_archive_row(row) if isinstance(row, dict) else row
            for row in rows
        ]

    async def _aread() -> list[dict]:
        return await asyncio.to_thread(_read)

    try:
        return await _aload_pool_cached(path, _aread)
    except FileNotFoundError:
        # Missing archive is normal for new characters / no prior archival, previously handled
        # by explicit os.path.exists. _file_identity's stat now provides
        # same answer without extra disk probe, and must not become WARNING.
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[hybrid_recall] %s: 加载 facts_archive 失败: %s", lanlan_name, exc,
        )
        return []


async def _aload_reflections_for_recall(
    reflection_engine, lanlan_name: str,
) -> list[dict]:
    """``aload_reflections`` with the same file-identity parse cache.

    Wrapped here rather than inside ``ReflectionPersistence`` on purpose: the
    engine's own loader has many callers across refine / promotion / evidence
    loops, and some of them mutate the rows they get back. The recall path does
    not (see the module note above), so the cache is scoped to it.
    """
    async def _aload() -> list[dict]:
        return await reflection_engine.aload_reflections(lanlan_name) or []

    try:
        path = reflection_engine._reflections_path(lanlan_name)
    except Exception as exc:
        logger.debug(
            "[hybrid_recall] %s: 无法解析 reflections 路径，跳过缓存直读: %s",
            lanlan_name, exc,
        )
        return await _aload()

    return await _aload_pool_cached(path, _aload)


def _render_reflection_split(reflections: list[dict]) -> tuple[list[dict], list[dict]]:
    """The pending and the active confirmed reflections among ``reflections``,
    as ``aget_pending_reflections`` / ``aget_confirmed_reflections`` return
    them for the private scope.

    Recall already holds the parsed reflections (``_aload_reflections_for_recall``,
    served from the parse cache); asking the engine instead would read and
    parse ``reflections.json`` twice more. Nothing here mutates the rows.
    """
    from memory._reflection.selection import filter_active_confirmed
    from memory.scopes import filter_entries_for_subjects

    rows = [row for row in reflections or () if isinstance(row, dict)]
    pending = [row for row in rows if row.get('status') == 'pending']
    confirmed = filter_active_confirmed(rows, now=datetime.now(), score=evidence_score)
    return (
        filter_entries_for_subjects(pending, None, include_legacy_private=None),
        filter_entries_for_subjects(confirmed, None, include_legacy_private=None),
    )


def _drop_archive_overlap(
    archive_rows: list[dict], active_rows: list[dict], lanlan_name: str,
) -> list[dict]:
    """Drop archived rows whose id is still present among the active facts.

    ``FactStore._archive_absorbed`` commits two files that cannot be written
    atomically together (facts_archive.json, then facts.json) and deliberately
    prefers the "row is in both files" interruption state over "row is in
    neither". Collapsing that overlap is therefore every archive reader's job,
    not an optional extra guard; ``FactStore.load_facts_full`` does the same
    thing for its own callers.

    Here the overlap distorts scoring, not just row counts: ``_rrf_fuse`` adds
    ``1/(k+rank)`` for every occurrence of an id, so the duplicated row scores
    twice, and each copy also occupies one of the ``HYBRID_RECALL_BUDGET_EACH``
    slots, pushing a genuine candidate out of the fused result.
    ``recall_by_time`` has no fusion step at all and would simply return the
    same memory twice.

    Active wins for the same reason it wins in ``load_facts_full``: it is at
    least as fresh as the archived copy, and monotonic flags (``absorbed`` /
    ``signal_processed``) are authoritative there.
    """
    if not archive_rows:
        return []
    # Reuse facts.py's usable-id check: manual/legacy ids may be missing, empty, or
    # list/dict; inconsistent checks leave rows the other path assumes deduplicated. Lazy import follows
    # this file's memory.* convention to avoid startup import cycles.
    from memory.facts import _readable_fact_id

    active_ids = set()
    for row in active_rows or []:
        if not isinstance(row, dict):
            continue
        fid = _readable_fact_id(row)
        if fid is not None:
            active_ids.add(fid)
    if not active_ids:
        return list(archive_rows)
    out: list[dict] = []
    dropped = 0
    for row in archive_rows:
        if isinstance(row, dict) and _readable_fact_id(row) in active_ids:
            dropped += 1
            continue
        out.append(row)
    if dropped:
        logger.warning(
            "[hybrid_recall] %s: facts_archive 有 %d 条 id 与活跃 facts 重叠，"
            "已按活跃副本收敛（多半是上一次归档两文件提交被打断）",
            lanlan_name, dropped,
        )
    return out


def _tag_tier(
    items: list[dict], tier: str, *, now: datetime | None = None,
) -> list[dict]:
    """Shallow-copy each item and stamp ``_tier`` + ``target_type`` (+ ``score``
    for reflections) for downstream hard_filter + result formatting. Doesn't
    mutate originals.

    Skip non-dict rows defensively: facts.json / reflections.json /
    facts_archive.json are all nominally list[dict] schemas, but manual edits /
    legacy leftovers / migration bugs can slip non-dicts (string / int / list)
    into the list. ``dict(it)`` would TypeError / ValueError on those, and a
    single bad row would take down the whole _tag_tier → the entire
    hybrid_recall aborts, violating the "skip the single bad row, return the
    rest" design. Codex review on PR #1385.
    """
    # Warning: compute ``score`` **now**, never assume row stores it. ``_hard_filter`` claims
    # drop score<0 but reads ``o.get('score')``, while reflections.json's persisted
    # schema (memory/_reflection/schema.py) has no ``score``: evidence comprises
    # ``reinforcement`` / ``disputation`` accumulators, with score derived on read via
    # ``evidence_score()``. Real data thus has ``score is None``, bypassing
    # filtering entirely, so user-disputed negatively scored reflections still reach recall_memory
    # and inject original text into context against system-prompt bans.
    #
    # Stage-2 signal pool (memory/facts.py:_aload_signal_targets) already does this
    # (``'score': evidence_score(r, now)`` at assembly); filtering there always
    # worked. Align semantics here so both recall surfaces agree.
    #
    # Warning: compute **only for reflections**. fact / fact_archive do not participate in evidence
    # (facts.json lacks reinforcement/disputation, and no writer sends them
    # signals); constant-zero score is noise. More importantly, ``evidence_score`` returns
    # +inf for ``protected``; if facts gain these fields later, define semantics explicitly in that change,
    # rather than inherit an incidental generalization here.
    #
    # Monotonicity: only **more** rows dropped, never more retained. ``score=None`` previously passed;
    # derived ≥0 still passes; only true negatives are blocked.
    if now is None:
        now = datetime.now()
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            # Avoid WARNING spam on repeated malformed hits; backend diagnosis
            # can inspect DEBUG. Even ``id`` unreadable, so log type only.
            logger.debug(
                "[hybrid_recall] _tag_tier: skipping non-dict %s entry (type=%s)",
                tier, type(it).__name__,
            )
            continue
        d = dict(it)
        d['_tier'] = tier
        # MemoryRecallReranker._hard_filter looks at target_type='reflection'
        # to drop terminal-status reflections. Tag explicitly so the filter
        # sees uniform shape.
        if tier == 'reflection':
            d.setdefault('target_type', 'reflection')
            # Warning: assign score **only to actually disputed rows** (``disputation > 0``).
            #
            # ``_hard_filter`` says 'user has been disputing this
            # more than confirming it', but ``evidence_score`` can become negative several ways:
            # when user stays **silent** about surfaced reflections, post_turn records ignored as
            # ``reinforcement += -0.2`` (IGNORED_REINFORCEMENT_DELTA). If AI
            # surfaces7 reflections and user does not engage this turn, all7 have rein=-0.2 /disp=0,
            # negative rows erased immediately by net-score filtering (adversarial audit found
            # 17.8% of active reflections in this category).
            #
            # Not engaging is not disagreement. Product semantics: silence prevents promotion,
            # not denies validity. Two weeks later a user asking what they were busy with last month should recall
            # exactly these entries. Require ``disputation > 0`` to match filtering
            # docstring: **disputed at least once, with disputes outweighing confirmations**.
            #
            # Leave rows without disputation **untouched**: real reflections.json
            # lacks score, so untouched means no key and unscored filtering fallback, as intended.
            #
            # Warning: previously ``d.pop('score', None)`` reversed this. Before change,
            # ``_tag_tier`` never touched this key, so consistent previous behavior requires **leaving it**,
            # not deletion: stale persisted ``score: -5.0`` rows (acknowledged by comment below)
            # would be dropped by ``_hard_filter`` but pop would
            # **resurrect** them, contradicting only-more-drops monotonicity.
            # Observed ``{"id":"stale_neg","status":"confirmed","score":-5.0}``:
            # direct _hard_filter gives [], but _tag_tier then filter gives ['stale_neg'].
            try:
                if float(d.get('disputation', 0.0) or 0.0) > 0.0:
                    # Warning: overwrite, not setdefault; persisted ``score`` means
                    # legacy/manual stale snapshot with incorrect decay.
                    # Only fresh computation is authoritative.
                    d['score'] = evidence_score(d, now)
            except Exception as exc:
                # Invalid fields in one row (``reinforcement: "abc"``) must not break entire pool;
                # same policy as non-dict skipping here and ``_hard_filter``'s per-entry
                # try/except. Again **do not remove** score if current calculation fails;
                # retaining persisted value most closely matches this function never running.
                logger.debug(
                    "[hybrid_recall] _tag_tier: score unavailable for "
                    "reflection id=%r: %s: %s",
                    d.get('id'), type(exc).__name__, exc,
                )
        out.append(d)
    return out


def _entry_event_window(entry: dict):
    """Get the entry's event time interval ``(start, end)`` (naive local). Returns
    None when no parsable timestamp exists.

    Anchor priority matches the persona stale block / ``temporal._past_anchor``:
    ``event_end_at → event_start_at → created_at``. ``created_at`` (write time)
    is only the last resort when there is no event time at all; entries with
    only ``event_end_at`` anchor on end — they are neither misjudged as
    timeless nor windowed/sorted by write time (CodeRabbit).

    - both ends present → [start, end]
    - only start → [start, start]
    - only end   → [end, end]
    - neither    → [created_at, created_at]

    fact / reflection share the same anchor fields (schema v2), unifying the
    time-filter/sort semantics. Archived facts go through this too.
    """
    from memory.temporal import _parse_iso_safe, to_naive_local
    # Convert aware imports/migrations (+00:00 / Z) to local before stripping tz, keeping day-level windows
    # assigned to correct dates at boundaries (Codex). to_naive_local leaves naive /None unchanged.
    start = to_naive_local(_parse_iso_safe(entry.get('event_start_at')))
    end = to_naive_local(_parse_iso_safe(entry.get('event_end_at')))
    created = to_naive_local(_parse_iso_safe(entry.get('created_at')))
    anchor = end or start or created
    if anchor is None:
        return None
    s = start or anchor
    e = end or anchor
    if e < s:  # Guard reversed start/end from manual edits / migrations.
        s, e = e, s
    return (s, e)


def _overlaps_window(entry: dict, win_start, win_end) -> bool:
    """Whether the entry's event interval [s, e] intersects the half-open window
    [win_start, win_end). Entries without a parsable timestamp are treated as
    outside the window (for time-based recall, better to miss than to mismatch).
    """
    win = _entry_event_window(entry)
    if win is None:
        return False
    s, e = win
    return s < win_end and e >= win_start


# ── public entry ──────────────────────────────────────────────────────


async def hybrid_recall(
    *,
    lanlan_name: str,
    query: str,
    fact_store,
    reflection_engine,
    config_manager,
    time_window: tuple | None = None,
    subjects=None,
    include_legacy_private: bool | None = None,
    persona_manager=None,
    budget_ms: int | None = None,
) -> dict[str, Any]:
    """End-to-end hybrid recall — the function the ``/query_memory``
    HTTP endpoint should call.

    Args:
      lanlan_name: per-character data scope key
      query: natural-language query string from the model's
        ``recall_memory(query=...)`` arg
      fact_store: ``memory.facts.FactStore`` instance (memory_server's module-
        level global)
      reflection_engine: ``memory.reflection.ReflectionEngine`` instance
      config_manager: needed by ``collect_stop_names`` to derive the
        master/lanlan name filter
      time_window: optional ``(start, end)`` naive time interval (from the
        joint "semantic + time" recall where ``recall_memory(query=...,
        time=...)`` supplies both). When given, the candidate pool is
        **hard-filtered by the event-time window first**, then the regular
        BM25 + cosine + RRF runs over the in-window entries — i.e. "memories
        related to the query within that period". When absent, full-corpus
        semantic recall (old behavior).

    Returns:
      ::

        {
          "results": [
            {
              "id": "fact_xxx",
              "text": "original memory text (not translated)",
              "tier": "fact" | "reflection" | "fact_archive",
              "entity": "master" | "neko" | "relationship" | null,
              "score": 0.0327,    # RRF fused score, for observability
              "created_at": "2026-05-01T...",
            },
            ...
          ],
          "query": str,
          "candidates_total": int,    # union pool size after hard_filter
          "elapsed_ms": float,
        }
    """
    start = time.time()

    # Empty query short-circuit — model occasionally calls with empty args
    # when it just wants to "check if recall is available". Avoid hitting
    # disk for that.
    if not query or not query.strip():
        return {
            "results": [], "query": query or "",
            "candidates_total": 0, "elapsed_ms": 0.0,
        }

    # Load pools concurrently. facts is served from FactStore's own process
    # cache; the other two from the file-identity parse cache (#2550) — on a
    # steady-state corpus all three collapse to a stat() apiece.
    active_facts, active_reflections, archive_facts = await asyncio.gather(
        fact_store.aload_facts(lanlan_name),
        _aload_reflections_for_recall(reflection_engine, lanlan_name),
        _aload_archive_facts(fact_store, lanlan_name),
    )
    active_facts = active_facts or []
    active_reflections = active_reflections or []
    archive_facts = _drop_archive_overlap(
        archive_facts or [], active_facts, lanlan_name,
    )
    # Persona entries the private render leaves out of the system prompt
    # (over its budget) are recallable like any other memory; the ones it
    # keeps are already in front of the model.
    persona_left_out: list[dict] = []
    if persona_manager is not None and not subjects:
        try:
            pending, confirmed = _render_reflection_split(active_reflections)
            persona_left_out, _resident = await persona_manager.arecall_residency(
                lanlan_name, pending, confirmed,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[hybrid_recall] persona residency skipped: %s", exc)
            persona_left_out = []

    facts_tagged = _tag_tier(active_facts, 'fact')
    refl_tagged = _tag_tier(active_reflections, 'reflection')
    arch_tagged = _tag_tier(archive_facts, 'fact_archive')
    persona_tagged = _tag_tier(persona_left_out, 'persona')

    # Security boundary: scope filtering happens before any BM25/cosine/RRF
    # scoring. A group caller never searches the private/global corpus and then
    # hides results afterwards. With no subjects, missing scope keeps the legacy
    # private behaviour; explicit subjects exclude legacy rows by default.
    from memory.scopes import filter_entries_for_subjects
    facts_tagged = filter_entries_for_subjects(
        facts_tagged, subjects, include_legacy_private=include_legacy_private,
    )
    refl_tagged = filter_entries_for_subjects(
        refl_tagged, subjects, include_legacy_private=include_legacy_private,
    )
    arch_tagged = filter_entries_for_subjects(
        arch_tagged, subjects, include_legacy_private=include_legacy_private,
    )
    persona_tagged = filter_entries_for_subjects(
        persona_tagged, subjects, include_legacy_private=include_legacy_private,
    )

    # Joint semantic + time retrieval: given time_window, hard-filter candidate pool by event time
    # before subsequent BM25/cosine scoring.
    if time_window is not None:
        ws, we = time_window
        facts_tagged = [d for d in facts_tagged if _overlaps_window(d, ws, we)]
        refl_tagged = [d for d in refl_tagged if _overlaps_window(d, ws, we)]
        arch_tagged = [d for d in arch_tagged if _overlaps_window(d, ws, we)]
        persona_tagged = [d for d in persona_tagged if _overlaps_window(d, ws, we)]

    bm25_pool_raw = facts_tagged + refl_tagged + arch_tagged + persona_tagged
    embedding_pool_raw = facts_tagged + refl_tagged + persona_tagged

    # Hard filter (drop score<0 / suppressed / terminal reflection / protected).
    # Imported lazily so a circular-import-safe boot path stays viable.
    from memory.recall import MemoryRecallReranker
    bm25_pool = MemoryRecallReranker._hard_filter(bm25_pool_raw)
    embedding_pool = MemoryRecallReranker._hard_filter(embedding_pool_raw)

    # stop_names: strip master/catgirl nicknames so high-DF entity names do not
    # dominate BM25 IDF.
    try:
        from memory.stop_names import collect_stop_names
        stop_names = collect_stop_names(config_manager) or []
    except Exception as exc:
        logger.debug("[hybrid_recall] collect_stop_names skipped: %s", exc)
        stop_names = []

    # Score. BM25 is sync + cheap (≤ few-hundred docs, pure-Python loop);
    # cosine is async (embed_batch). Run cosine first so the BM25 work
    # can proceed while embedding model warms (if first call).
    cosine_scored_task = asyncio.create_task(_cosine_rank(query, embedding_pool))
    bm25_scored = _bm25_rank(query, bm25_pool, stop_names=stop_names)
    if budget_ms is None:
        cosine_scored = await cosine_scored_task
    else:
        # Past the caller's budget the vector half is dropped and the
        # keyword half still answers, instead of the whole call timing out.
        remaining = max(int(budget_ms), 0) / 1000 - (time.time() - start)
        done, _pending = await asyncio.wait({cosine_scored_task}, timeout=max(remaining, 0))
        if cosine_scored_task in done:
            cosine_scored = cosine_scored_task.result()
        else:
            cosine_scored_task.cancel()
            cosine_scored = []
            logger.info("[hybrid_recall] %s: vector half over budget, keywords only", lanlan_name)

    # Threshold + per-side cap.
    bm25_top = [
        (d, s) for d, s in bm25_scored if s >= HYBRID_RECALL_BM25_THRESHOLD
    ][:HYBRID_RECALL_BUDGET_EACH]
    cosine_top = [
        (d, s) for d, s in cosine_scored if s >= HYBRID_RECALL_COSINE_THRESHOLD
    ][:HYBRID_RECALL_BUDGET_EACH]

    fused = _rrf_fuse(
        bm25_top, cosine_top,
        k=HYBRID_RECALL_RRF_K,
        budget_total=HYBRID_RECALL_BUDGET_TOTAL,
    )

    results = [
        {
            "id": d.get('id') or '',
            "text": d.get('text') or '',
            "tier": d.get('_tier') or 'unknown',
            "entity": d.get('entity'),
            "subject_kind": d.get('subject_kind'),
            "subject_id": d.get('subject_id'),
            "scope": d.get('scope') or 'legacy_private',
            "score": round(d.get('_rrf_score', 0.0), 6),
            # created_at is memory persistence time; event_start/end_at are actual event
            # anchors (schema v2, derived from event_when_raw). They can differ greatly
            # (last week's all-nighter recorded today), so include both, preferring event
            # anchors when rendering when events happened.
            "created_at": d.get('created_at'),
            "event_start_at": d.get('event_start_at'),
            "event_end_at": d.get('event_end_at'),
        }
        for d in fused
    ]

    elapsed_ms = (time.time() - start) * 1000.0
    # Embedding-service state: log why embedding path gives zero directly. Otherwise
    # ``emb=0`` looks identical for disabled service versus running service with no cached
    # vectors, requiring embeddings.py logs to distinguish,
    # historically absent from Memory log file. Reading service state does not load or
    # throw (pure properties); failures degrade to unknown without affecting recall.
    try:
        from memory.embeddings import get_embedding_service
        _svc = get_embedding_service()
        emb_state = "ready" if _svc.is_available() else (
            "disabled:%s" % _svc.disable_reason() if _svc.is_disabled() else "not_ready"
        )
    except Exception as exc:  # noqa: BLE001
        emb_state = "unknown(%s)" % type(exc).__name__
    # union pool size for observability — bm25 pool is the superset.
    # `passed` = items surviving the per-side threshold; `thresh` is the
    # Cutoff constant. Previously log put `passed` count into `(>thresh %d)`,
    # misleading readers into threshold=N and wrong tuning; separate passed and thresh.
    logger.info(
        "[hybrid_recall] %s: pool bm25=%d emb=%d | "
        "scored bm25=%d (passed %d, thresh=%.2f) "
        "emb=%d (passed %d, thresh=%.2f) | emb_svc=%s | fused=%d | %.0fms",
        lanlan_name,
        len(bm25_pool), len(embedding_pool),
        len(bm25_scored), len(bm25_top), HYBRID_RECALL_BM25_THRESHOLD,
        len(cosine_scored), len(cosine_top), HYBRID_RECALL_COSINE_THRESHOLD,
        emb_state,
        len(results), elapsed_ms,
    )
    return {
        "results": results,
        "query": query,
        "candidates_total": len(bm25_pool),
        "elapsed_ms": round(elapsed_ms, 1),
    }


async def recall_by_time(
    *,
    lanlan_name: str,
    time_spec: str,
    fact_store,
    reflection_engine,
    subjects=None,
    include_legacy_private: bool | None = None,
) -> dict[str, Any]:
    """Time-based recall — return the few entries (facts + reflections mixed)
    whose event time is **closest to the ``time_spec`` window**.

    This is the path taken by ``recall_memory(time=...)``: no BM25 / cosine
    semantic scoring; sorted purely by event-time anchors, so the memories of
    "that day / that week / that period" all get pulled up without being
    dropped for not semantically matching a query — and no query is needed,
    time alone suffices.

    Sort semantics: take each entry's event window ``[event_start_at,
    event_end_at]`` (missing start falls back to ``created_at``, missing end
    falls back to start) and compute its time distance to the
    ``[win_start, win_end)`` window resolved by ``parse_time_window`` —
    distance 0 inside the window, otherwise seconds to the nearest window
    boundary. Sort by (distance ASC, event start DESC) and take the first
    ``HYBRID_RECALL_TIME_BUDGET`` entries. In-window entries (distance 0)
    naturally rank first; with an empty window the result degrades to the
    temporally nearest few.

    Candidate pool = active facts + active reflections + archived facts
    (archives matter when recalling old periods), then ``_hard_filter`` drops
    score<0 / suppressed / terminal entries, consistent with
    ``hybrid_recall``. Returns empty results when ``time_spec`` fails to parse.
    """
    from memory.temporal import parse_time_window
    start_t = time.time()
    window = parse_time_window(time_spec)
    if window is None:
        logger.info(
            "[recall_by_time] %s: unparseable time=%r → empty", lanlan_name, time_spec,
        )
        return {
            "results": [], "query": "", "time": time_spec or "",
            "candidates_total": 0, "elapsed_ms": 0.0,
        }
    win_start, win_end = window

    active_facts, active_reflections, archive_facts = await asyncio.gather(
        fact_store.aload_facts(lanlan_name),
        _aload_reflections_for_recall(reflection_engine, lanlan_name),
        _aload_archive_facts(fact_store, lanlan_name),
    )
    active_facts = active_facts or []
    pool_raw = (
        _tag_tier(active_facts, 'fact')
        + _tag_tier(active_reflections or [], 'reflection')
        + _tag_tier(
            _drop_archive_overlap(archive_facts or [], active_facts, lanlan_name),
            'fact_archive',
        )
    )
    from memory.scopes import filter_entries_for_subjects
    pool_raw = filter_entries_for_subjects(
        pool_raw, subjects, include_legacy_private=include_legacy_private,
    )
    from memory.recall import MemoryRecallReranker
    pool = MemoryRecallReranker._hard_filter(pool_raw)

    # (outside-window flag0/1, distance seconds, event start s, doc).
    scored: list[tuple[int, float, datetime, dict]] = []
    for d in pool:
        win = _entry_event_window(d)
        if win is None:
            continue
        s, e = win
        # Half-open [win_start, win_end): event closed interval [s,e] is in-window if intersecting.
        in_window = s < win_end and e >= win_start
        if in_window:
            dist = 0.0
        elif e < win_start:          # Entire event before window.
            dist = (win_start - e).total_seconds()
        else:                        # Entire event at/after right edge win_end.
            dist = (s - win_end).total_seconds()
        # Primary in_window key first: right-edge event (s==win_end, outside half-open window)
        # also has dist0; distance alone ties actual in-window entries and secondary keys can
        # rank it first. Primary0/1 guarantees in-window before outside (Codex).
        scored.append((0 if in_window else 1, dist, s, d))

    # Secondary distance ascending; tertiary (win_start - s) timedelta ascending = s descending,
    # putting recent events first without datetime.timestamp() (naive local time, ambiguous DST,
    # pre-1970 Windows OSError). Exclude doc from key to avoid dict comparison.
    scored.sort(key=lambda t: (t[0], t[1], win_start - t[2]))
    top = scored[:HYBRID_RECALL_TIME_BUDGET]
    results = [
        {
            "id": d.get('id') or '',
            "text": d.get('text') or '',
            "tier": d.get('_tier') or 'unknown',
            "entity": d.get('entity'),
            "subject_kind": d.get('subject_kind'),
            "subject_id": d.get('subject_id'),
            "scope": d.get('scope') or 'legacy_private',
            "score": None,  # Temporal path has no semantic scoring.
            "created_at": d.get('created_at'),
            "event_start_at": d.get('event_start_at'),
            "event_end_at": d.get('event_end_at'),
        }
        for _, _, _, d in top
    ]
    elapsed_ms = (time.time() - start_t) * 1000.0
    logger.info(
        "[recall_by_time] %s: time=%s window=[%s,%s) pool=%d returned=%d | %.0fms",
        lanlan_name, time_spec,
        win_start.isoformat(), win_end.isoformat(),
        len(pool), len(results), elapsed_ms,
    )
    return {
        "results": results,
        "query": "",
        "time": time_spec,
        "candidates_total": len(pool),
        "elapsed_ms": round(elapsed_ms, 1),
    }
