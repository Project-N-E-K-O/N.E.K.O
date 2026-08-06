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
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
from collections import OrderedDict
from typing import Any

from config import (
    HYBRID_RECALL_BM25_THRESHOLD,
    HYBRID_RECALL_BUDGET_EACH,
    HYBRID_RECALL_BUDGET_TOTAL,
    HYBRID_RECALL_COSINE_THRESHOLD,
    HYBRID_RECALL_POOL_CACHE_MAX_FILES,
    HYBRID_RECALL_RRF_K,
    HYBRID_RECALL_TIME_BUDGET,
)
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
    duplicate terms within a doc get deduped → BM25's TF signal dies ("博士"
    appearing 5 times scores the same as once; BM25 degrades into BM1). The set
    semantics of ``_extract_keywords`` serve the ``_is_mentioned`` /
    ``anti_repeat`` use cases ("did it appear", not caring how many times),
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
    never has to guess which script the user "meant" — it only has to make
    the two agree. It runs *before* stop-name stripping, and the stop names
    are folded with it, so a name configured in one script still strips out
    of text written in the other.
    """  # noqa: DOCSTRING_CJK
    # Lazy import: 跟着 _extract_keywords 一起借 _SPLIT_RE 和 strip_stop_names，
    # 不要硬依赖 import-time —— persona 在某些 entrypoint（memory-only test）
    # 可能没加载。
    try:
        from memory.persona import _SPLIT_RE, strip_stop_names
    except Exception as exc:
        logger.warning(
            "[hybrid_recall] tokenize fallback to whitespace split: %s",
            exc,
        )
        # str() coerce 防 malformed entry 里 text 是 int / list 等 truthy
        # non-string（codex review #1 之前那条）。fold 也要跟上——降级路径
        # 同样服务繁简两侧的 query/doc，漏了就是降级时静默退回 #2584。
        return [t for t in fold_script(str(text or "")).split() if len(t) >= 2]

    # str() coerce 同 fallback 路径——malformed memory entry 里 text 可能
    # 是 list / int 等 truthy non-string，传给 _SPLIT_RE.split 会 TypeError
    # 把整条 hybrid_recall abort（应该只 skip 这一行，不该带挂全 query）。
    # codex review (3rd round): normal path 之前漏 coerce，只在 fallback 做。
    # 折叠放在 strip 之前，且 stop_names 一起折：两边都落到简体空间，繁体
    # fact 里的简体配置名（或反之）才strip得掉。fold 是 1:1 定长映射，不动
    # 标点，所以下面 _SPLIT_RE 的切分位置不受影响。
    raw_text = fold_script(str(text or ""))
    if stop_names:
        try:
            raw_text = strip_stop_names(
                raw_text, [fold_script(str(n)) for n in stop_names],
            )
        except Exception:
            # strip_stop_names 内部就是字符串替换，理论上不会挂；保险起见
            # 不挂 BM25 主流程。
            pass

    out: list[str] = []
    for seg in _SPLIT_RE.split(raw_text):
        seg = seg.strip()
        if not seg:
            continue
        # CJK 占比阈值 = 与 persona._extract_keywords 完全一致（汉字 +
        # 假名 + 谚文 = U+4E00-9FFF + U+3040-30FF + U+AC00-D7AF）。
        cjk_count = sum(
            1 for ch in seg
            if '一' <= ch <= '鿿'
            or '぀' <= ch <= 'ヿ'
            or '가' <= ch <= '힯'
        )
        if cjk_count > len(seg) // 2:
            # CJK 段：2-gram + 3-gram 滑窗，**append 不去重**，留 TF。
            for n in (2, 3):
                for i in range(len(seg) - n + 1):
                    out.append(seg[i:i + n])
        else:
            # Latin 段：整段做一个 token（len >= 2 才要），同样 append。
            if len(seg) >= 2:
                out.append(seg)
    return out


# ── BM25 retrieval ────────────────────────────────────────────────────


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
    if not query or not pool:
        return []
    query_terms = _tokenize(query, stop_names)
    if not query_terms:
        return []

    # Tokenize all docs once; reuse the same call for DF and TF.
    doc_terms_list: list[list[str]] = [
        _tokenize(d.get('text', '') or '', stop_names) for d in pool
    ]

    n_docs = len(pool)
    total_len = sum(len(t) for t in doc_terms_list)
    if total_len == 0:
        return []
    avgdl = total_len / n_docs

    query_unique = set(query_terms)

    # DF + per-doc TF, **restricted to query terms** (#2550).
    #
    # 这里以前是「先建全语料 df 表（每 doc 一个 set(terms)）+ 每 doc 一个
    # Counter(terms)」。两张表都是全词表规模，但打分只会去查 query 里那几十个
    # 词——5000 条语料下 df 表有 ~10.9 万项、查得到的不到 0.03%，Counter 同理。
    # 实测这两步合计 80ms / 160ms，比分词本身（46ms）还贵。
    #
    # 改成单趟扫描、只累计落在 query_unique 里的词：df 由 tf 直接派生（doc 里
    # 出现过 ⇔ tf 有键），语义与 set(terms) 去重计数完全一致。5000 条实测
    # 160ms → 61ms。得分逐位不变——下面的打分循环仍按 ``query_unique`` 迭代，
    # 浮点累加顺序没动（换成迭代 tf 会改累加顺序，末位 ULP 可能漂，并列时理论
    # 上能翻排序，所以别顺手"优化"成那样）。
    df: dict[str, int] = dict.fromkeys(query_unique, 0)
    doc_tf_list: list[dict[str, int]] = []
    for terms in doc_terms_list:
        tf_map: dict[str, int] = {}
        for t in terms:
            if t in query_unique:
                tf_map[t] = tf_map.get(t, 0) + 1
        doc_tf_list.append(tf_map)
        for t in tf_map:
            df[t] += 1

    scored: list[tuple[dict, float]] = []
    for doc, doc_terms, doc_tf in zip(pool, doc_terms_list, doc_tf_list):
        if not doc_terms:
            continue
        dl = len(doc_terms)
        norm = 1.0 - b + b * dl / avgdl
        score = 0.0
        for q_term in query_unique:
            n = df.get(q_term, 0)
            if n <= 0:
                continue
            # Robertson-Sparck-Jones IDF with +0.5 smoothing.
            idf = math.log((n_docs - n + 0.5) / (n + 0.5) + 1.0)
            if idf <= 0:
                continue
            tf = doc_tf.get(q_term, 0)
            if tf == 0:
                continue
            score += idf * (tf * (k1 + 1)) / (tf + k1 * norm)
        if score > 0:
            scored.append((doc, score))

    scored.sort(key=lambda p: p[1], reverse=True)
    return scored


# ── cosine retrieval ──────────────────────────────────────────────────


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
        decode_valid_cached_embedding,
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
    # failure（embed_batch 抛 / numpy 缺 / 单条 doc 解码崩）不把已经算出
    # 的 BM25 结果一起埋了。上游 ``hybrid_recall`` await 这条 task 时
    # 如果异常就丢 BM25 → 退化为空召回，违背 hybrid 的初衷。
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

        scored: list[tuple[dict, float]] = []
        for doc in pool:
            text = doc.get('text', '') or ''
            # 一次解码拿到向量，而不是「is_cached_embedding_valid 里解一遍判维度
            # → 丢掉 → decode_embedding 再解一遍」。两次 base64 解码 + 两次
            # np.isfinite 全扫在 5000 条池子上实测约 26ms 纯白费（#2550）。
            # 维度判定语义不变：valid 判的是 model_id 解析出的期望维度，下面这行
            # 判的是 target_dim（model_id 解析失败时回落成 query 维度），后者仍是
            # 兜底那一路唯一的约束，不能省。
            cvec = decode_valid_cached_embedding(doc, text, model_id)
            if cvec is None or cvec.size != target_dim:
                continue
            carr = np.asarray(cvec, dtype=np.float32)
            cnorm = float(np.linalg.norm(carr))
            if cnorm <= 0:
                continue
            cos = float(np.dot(qarr, carr) / (qnorm * cnorm))
            scored.append((doc, cos))

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
    """
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
        # subject 时间归档（subject_archived_at 标记）的行不进召回：
        # absorbed 归档行留在池里是设计（BM25 长尾），但 subject 归档
        # 的语义就是「这个群/成员的记忆整体退出候选」——这里是两条召
        # 回路径（hybrid_recall / recall_by_time）唯一的 archive 池装
        # 配点，单点过滤即可覆盖。恢复路径会剥掉标记，行自然回池。
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
        # 投影到召回真正读的字段（见 _ARCHIVE_RECALL_KEYS）。这一步同时把向量
        # 剥掉了——归档明确不进向量池（见模块顶部 Pool composition），那列占了
        # 文件 ~12/13 的体积，解析出来只为被无视，还要一直挂在上面那份缓存里。
        # 落盘那份保持原样：恢复路径把行搬回 active 时仍要拿到缓存向量、以及
        # 其余所有写侧字段，所以剥离只发生在"进内存"这一刻。
        return [
            _project_archive_row(row) if isinstance(row, dict) else row
            for row in rows
        ]

    async def _aread() -> list[dict]:
        return await asyncio.to_thread(_read)

    try:
        return await _aload_pool_cached(path, _aread)
    except FileNotFoundError:
        # "还没有归档文件" 是常态而非故障（新角色 / 从未触发过归档），以前由
        # 一次显式 os.path.exists 提前拦掉；现在 _file_identity 的 stat 已经是
        # 同一个答案，不必再多探一次盘，但也不该升级成 WARNING。
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
    # 复用 facts.py 的「可用 id」判定：手改 / 老库里的 id 可能缺失、为空串或是
    # list/dict，两边判得不一样就会各自留下对方以为已收敛的行。惰性导入沿用本
    # 文件对 memory.* 的一贯写法（避免启动期循环导入）。
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


def _tag_tier(items: list[dict], tier: str) -> list[dict]:
    """Shallow-copy each item and stamp ``_tier`` + ``target_type`` for
    downstream hard_filter + result formatting. Doesn't mutate originals.

    Skip non-dict rows defensively: facts.json / reflections.json /
    facts_archive.json are all nominally list[dict] schemas, but manual edits /
    legacy leftovers / migration bugs can slip non-dicts (string / int / list)
    into the list. ``dict(it)`` would TypeError / ValueError on those, and a
    single bad row would take down the whole _tag_tier → the entire
    hybrid_recall aborts, violating the "skip the single bad row, return the
    rest" design. Codex review on PR #1385.
    """
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            # 不打 WARNING 防止 malformed 连续命中刷屏；交给后端排查时
            # 看 DEBUG 即可。``id`` 字段都没法读出来，只能 log 类型。
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
    # aware（import/迁移的 +00:00 / Z）统一先转本地再剥 tz，避免 day 级窗口
    # 在日界处归错天（Codex）。to_naive_local 对 naive / None 是 no-op。
    start = to_naive_local(_parse_iso_safe(entry.get('event_start_at')))
    end = to_naive_local(_parse_iso_safe(entry.get('event_end_at')))
    created = to_naive_local(_parse_iso_safe(entry.get('created_at')))
    anchor = end or start or created
    if anchor is None:
        return None
    s = start or anchor
    e = end or anchor
    if e < s:  # 防 manual edit / 迁移把 start/end 写反
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

    facts_tagged = _tag_tier(active_facts, 'fact')
    refl_tagged = _tag_tier(active_reflections, 'reflection')
    arch_tagged = _tag_tier(archive_facts, 'fact_archive')

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

    # "语义 + 时间"联合检索：给了 time_window 就先把候选池按事件时间窗口
    # 硬过滤，只让落在该区间的条目进入后续 BM25 / cosine 打分。
    if time_window is not None:
        ws, we = time_window
        facts_tagged = [d for d in facts_tagged if _overlaps_window(d, ws, we)]
        refl_tagged = [d for d in refl_tagged if _overlaps_window(d, ws, we)]
        arch_tagged = [d for d in arch_tagged if _overlaps_window(d, ws, we)]

    bm25_pool_raw = facts_tagged + refl_tagged + arch_tagged
    embedding_pool_raw = facts_tagged + refl_tagged

    # Hard filter (drop score<0 / suppressed / terminal reflection / protected).
    # Imported lazily so a circular-import-safe boot path stays viable.
    from memory.recall import MemoryRecallReranker
    bm25_pool = MemoryRecallReranker._hard_filter(bm25_pool_raw)
    embedding_pool = MemoryRecallReranker._hard_filter(embedding_pool_raw)

    # stop_names: strip 主人/猫娘 nicknames so high-DF entity names don't
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
    cosine_scored = await cosine_scored_task

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
            # created_at = 记忆写盘时间；event_start/end_at = 事件真正发生
            # 的时间锚点（schema v2，由 event_when_raw 解算）。两者可能差很
            # 远（"上周通宵"今天才被写进记忆），所以都带上，渲染侧优先用
            # event 锚点回答"事件什么时候发生"。
            "created_at": d.get('created_at'),
            "event_start_at": d.get('event_start_at'),
            "event_end_at": d.get('event_end_at'),
        }
        for d in fused
    ]

    elapsed_ms = (time.time() - start) * 1000.0
    # 嵌入服务状态：把 emb 路径"为啥是 0"直接写进这条 log。否则
    # ``emb=0`` 在"服务没起来(disabled)"和"服务起来了但池子里一条向量都没
    # 缓存"两种情况下长得一模一样，排障时必须翻 embeddings.py 的日志才能区分
    # —— 而那条历史上还进不了 Memory 日志文件。读 service 状态不触发加载、
    # 不抛异常（纯属性读），失败也只是降级成 "unknown"，不影响召回结果。
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
    # cutoff constant. 历史上这条 log 把 `passed` 数挂在 `(>thresh %d)`
    # 字段里，被读成"阈值=N"误导调参，所以拆成 passed + thresh 两段。
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

    # (是否窗口外 0/1, 距离秒数, 事件起点 s, doc)。
    scored: list[tuple[int, float, datetime, dict]] = []
    for d in pool:
        win = _entry_event_window(d)
        if win is None:
            continue
        s, e = win
        # 半开窗口 [win_start, win_end)：事件闭区间 [s, e] 与之有交即在窗口内。
        in_window = s < win_end and e >= win_start
        if in_window:
            dist = 0.0
        elif e < win_start:          # 整体早于窗口
            dist = (win_start - e).total_seconds()
        else:                        # 整体在右界 win_end 当点或之后
            dist = (s - win_end).total_seconds()
        # 主键 in_window 摆第一位：右界事件（s == win_end，半开窗口判为窗口
        # 外）的 dist 也是 0，若只按 dist 排会和真窗口内条目并列、再被次键
        # 顶到前面——给个 0/1 主键保证"窗口内永远排在窗口外前面"（Codex）。
        scored.append((0 if in_window else 1, dist, s, d))

    # 次键 dist 升序；三键用 (win_start - s) timedelta 升序 = s 降序（近发生
    # 的在前），不用 datetime.timestamp()（naive 走本地时区、DST 含糊、
    # pre-1970 Windows 会 OSError）。doc 不进 key 避免比较 dict。
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
            "score": None,  # 时间路径无语义打分
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
