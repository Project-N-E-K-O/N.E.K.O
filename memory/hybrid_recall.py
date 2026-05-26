# -*- coding: utf-8 -*-
"""
Hybrid memory recall — BM25 + cosine embedding parallel retrieval with
Reciprocal Rank Fusion. The user-facing backend for the ``recall_memory``
tool that ``main_logic/core.py`` calls when the model emits a tool call.

Pool composition
================
- **BM25 pool**:      facts (active) + reflections (active) + facts_archive
  BM25 is cheap on small corpora; including archive lets the model surface
  long-tail keyword hits that have aged out of the live working set.

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
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
from collections import Counter
from typing import Any

from config import (
    HYBRID_RECALL_BM25_THRESHOLD,
    HYBRID_RECALL_BUDGET_EACH,
    HYBRID_RECALL_BUDGET_TOTAL,
    HYBRID_RECALL_COSINE_THRESHOLD,
    HYBRID_RECALL_RRF_K,
    HYBRID_RECALL_TIME_BUDGET,
)
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
    """BM25-friendly tokenize：与 ``memory.persona._extract_keywords``
    **共享同款 _SPLIT_RE + CJK n-gram + Latin 词切分规则**，但 *保留
    multiplicity*（list with duplicates，不去重）。

    为啥不复用 ``_extract_keywords``：那个返回 ``set[str]``，单 doc 内
    重复词被 dedupe → BM25 的 TF 信号死掉（"博士"出现 5 次和 1 次得分
    一样，BM25 退化成 BM1）。``_extract_keywords`` 的 set 语义是给
    ``_is_mentioned`` / ``anti_repeat`` 这两个用例用的（"是否出现过"，不
    care 出现几次），跟检索 BM25 的目标不一样。所以本模块本地实现一份
    list-语义的 tokenize，分词规则严格对齐 persona 那份（共用 _SPLIT_RE
    + 同样的 CJK 阈值 + 2/3-gram + Latin 整段切），未来 persona 那边改
    分词规则要同步过来。

    Codex review #1 (commit fd2b75fc4): 之前 ``Counter`` 优化其实没生效
    —— set 输入下每个 key 计数都是 1。这版让 multiplicity 真的传到 BM25。

    stop_names: strip 主人/猫娘 等 from text before tokenize，避免高频
    实体名污染 BM25 IDF。
    """
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
        # non-string（codex review #1 之前那条）。
        return [t for t in str(text or "").split() if len(t) >= 2]

    # str() coerce 同 fallback 路径——malformed memory entry 里 text 可能
    # 是 list / int 等 truthy non-string，传给 _SPLIT_RE.split 会 TypeError
    # 把整条 hybrid_recall abort（应该只 skip 这一行，不该带挂全 query）。
    # codex review (3rd round): normal path 之前漏 coerce，只在 fallback 做。
    raw_text = str(text or "")
    if stop_names:
        try:
            raw_text = strip_stop_names(raw_text, stop_names)
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

    # DF: number of docs containing each term (deduped within a doc).
    df: dict[str, int] = {}
    for terms in doc_terms_list:
        for t in set(terms):
            df[t] = df.get(t, 0) + 1

    query_unique = set(query_terms)
    # 预算每个 doc 的词频表：替代 inner loop 里的 O(N) ``doc_terms.count(q_term)``。
    # Pool 量级 ≤ 几百时 perf 差别可忽略，但 Counter 查表是 O(1) + 标准模式更干净。
    doc_tf_list: list[Counter] = [Counter(terms) for terms in doc_terms_list]

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
            tf = doc_tf[q_term]
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
        decode_embedding,
        get_embedding_service,
        is_cached_embedding_valid,
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
            if not is_cached_embedding_valid(doc, text, model_id):
                continue
            cvec = decode_embedding(doc.get('embedding'))
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


async def _aload_archive_facts(fact_store, lanlan_name: str) -> list[dict]:
    """Load ``facts_archive.json`` directly. Returns ``[]`` on missing /
    parse error — archive miss is non-fatal for recall.

    Reaches into ``fact_store._facts_archive_path`` because there's no
    public archive loader (the FactStore archives but never re-reads its
    own archive in its hot path).
    """
    try:
        path = fact_store._facts_archive_path(lanlan_name)
    except Exception as exc:
        logger.warning(
            "[hybrid_recall] %s: 无法解析 facts_archive 路径: %s",
            lanlan_name, exc,
        )
        return []
    if not await asyncio.to_thread(os.path.exists, path):
        return []
    try:
        def _read() -> list[dict]:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        return await asyncio.to_thread(_read)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[hybrid_recall] %s: 加载 facts_archive 失败: %s", lanlan_name, exc,
        )
        return []


def _tag_tier(items: list[dict], tier: str) -> list[dict]:
    """Shallow-copy each item and stamp ``_tier`` + ``target_type`` for
    downstream hard_filter + result formatting. Doesn't mutate originals.

    Skip non-dict rows defensively：facts.json / reflections.json /
    facts_archive.json 理论上 schema 都是 list[dict]，但 manual edit /
    老格式残留 / 迁移 bug 可能让 list 里混进 non-dict（string / int /
    list）。``dict(it)`` 对这些会 TypeError / ValueError，单条坏行就把
    整个 _tag_tier 挂掉 → 整次 hybrid_recall abort，违背"单坏行 skip
    其余正常返回"的设计。Codex review on PR #1385。
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
    """取条目的事件时间区间 ``(start, end)``（naive 本地）。无可解析时间戳
    返回 None。

    锚点优先级和 persona 过时 block / ``temporal._past_anchor`` 一致——
    ``event_end_at → event_start_at → created_at``。``created_at``（写盘时间）
    只在完全没有事件时间时才兜底；只有 ``event_end_at`` 的条目用 end 当锚点，
    不会被误判成无时间、也不会拿写盘时间入窗/排序（CodeRabbit）。

    - 双端齐全 → [start, end]
    - 只有 start → [start, start]
    - 只有 end   → [end, end]
    - 都没有     → [created_at, created_at]

    fact / reflection 共用同一套锚点字段（schema v2），按时间过滤/排序口径
    统一。归档 fact 也走这套。
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
    """条目事件区间 [s, e] 与半开窗口 [win_start, win_end) 是否有交集。
    无可解析时间戳的条目判为不在窗口内（时间检索下宁可漏不可错挂）。
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
      time_window: 可选 ``(start, end)`` naive 时间区间（来自
        ``recall_memory(query=..., time=...)`` 同时给两者的"语义 + 时间"
        联合检索）。给了就把候选池**先按事件时间窗口硬过滤**，再在窗口内
        条目上跑常规 BM25 + cosine + RRF —— 即"那段时间里和 query 相关的
        记忆"。不给则全量语义检索（旧行为）。

    Returns:
      ::

        {
          "results": [
            {
              "id": "fact_xxx",
              "text": "原始记忆文本（不翻译）",
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

    # Load pools concurrently — three independent JSON reads.
    active_facts, active_reflections, archive_facts = await asyncio.gather(
        fact_store.aload_facts(lanlan_name),
        reflection_engine.aload_reflections(lanlan_name),
        _aload_archive_facts(fact_store, lanlan_name),
    )
    active_facts = active_facts or []
    active_reflections = active_reflections or []

    facts_tagged = _tag_tier(active_facts, 'fact')
    refl_tagged = _tag_tier(active_reflections, 'reflection')
    arch_tagged = _tag_tier(archive_facts or [], 'fact_archive')

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
) -> dict[str, Any]:
    """按时间回溯——返回离 ``time_spec`` 窗口**最接近的若干条**记忆
    （fact + reflection 混合）。

    这是 ``recall_memory(time=...)`` 走的路径：不做 BM25 / cosine 语义打分，
    纯按事件时间锚点排序，所以"那天/那周/那段时间"的记忆都能被拎出来，不会
    因为语义不匹配 query 而漏掉，且不需要 query —— 只给 time 即可。

    排序口径：取每条的事件窗口 ``[event_start_at, event_end_at]``（缺 start
    退回 ``created_at``，缺 end 退回 start），算它与 ``parse_time_window``
    解出的 ``[win_start, win_end)`` 的时间距离——落在窗口内距离为 0，否则
    取到最近窗口边界的秒数。按 (距离 ASC, 事件起点 DESC) 排序后取前
    ``HYBRID_RECALL_TIME_BUDGET`` 条。所以窗口内的条目（距离 0）天然排在
    最前，窗口为空时则回落到时间上最邻近的几条。

    候选池 = 活跃 facts + 活跃 reflections + 归档 facts（归档对老时间回溯
    有用），再过 ``_hard_filter`` 丢 score<0 / suppressed / 终态，口径与
    ``hybrid_recall`` 一致。``time_spec`` 解析失败时返回空 results。
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
        reflection_engine.aload_reflections(lanlan_name),
        _aload_archive_facts(fact_store, lanlan_name),
    )
    pool_raw = (
        _tag_tier(active_facts or [], 'fact')
        + _tag_tier(active_reflections or [], 'reflection')
        + _tag_tier(archive_facts or [], 'fact_archive')
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
