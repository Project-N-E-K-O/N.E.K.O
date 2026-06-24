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
MemoryRecallReranker — vector pre-rank + LLM rerank for memory recall.

Step 3 of P2.  Replaces the "top-N by evidence_score" heuristic that
``FactStore._aload_signal_targets`` uses today to pick candidates for
the Stage-2 signal-detection LLM call.  The new pipeline is:

  1. **Hard filter** — exclude entries with ``evidence_score < 0``,
     suppressed entries, terminal-status reflections, and persona
     entries marked ``protected`` (character-card sources).  Same
     semantics as today's render-time filters.

  2. **Coarse rank (vectors)** — when the EmbeddingService is
     available AND the caller passed in query texts (typically the
     newly-extracted facts), embed the queries, compute max-cosine
     between each candidate and any query, sort DESC, keep the top
     ``budget * COARSE_OVERSAMPLE`` rows (default 3 ×).  Without an
     embedding service or without query texts, this stage is a no-op
     and the original list passes through.

  3. **Fine rank (LLM)** — when embeddings produced more than
     ``budget`` candidates AND the LLM rerank prompt is configured,
     send the candidate set + query texts to a small LLM call asking
     for the most relevant ``budget`` items.  ``evidence_score`` is
     surfaced in the prompt as an auxiliary signal ("this entry has
     been confirmed N times by the user") rather than mixed into the
     ranking math — the LLM weighs it together with semantic
     relevance.  When the LLM call fails or vectors are off, the
     coarse-rank order (or evidence_score when even coarse failed)
     is used as the final order.

Fallback: when ``EmbeddingService`` is permanently disabled, the
whole pipeline degrades to "evidence_score DESC + top ``budget``" —
exactly the current behaviour, no LLM call cost added.

Why vectors are a *prefilter*, not a replacement: cosine alone can't
tell ``主人喜欢猫`` ("the user likes cats") from ``主人讨厌猫`` ("the user
hates cats") (≈0.78), and "semantically near" entries that the user
didn't actually mean to bring up trigger false positives in Stage-2
signal detection (which would either reinforce or negate the wrong
observation).  Keeping the LLM as the arbiter means we save *prompt
tokens* (smaller candidate set in the Stage-2 prompt), not *LLM
calls* — RFC §3.4 stays unchanged in shape.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import logging
import re

try:
    from memory.embeddings import (
        decode_embedding,
        get_embedding_service,
        is_cached_embedding_valid,
        parse_dim_from_model_id,
    )
except ImportError:
    # See ``embedding_worker`` for context. With the disabled-service
    # stub, ``MemoryRecallReranker`` keeps working but skips the cosine
    # prefilter (decode_embedding returns None for every candidate) —
    # callers already handle that path via ``rerank=False`` semantics.
    from memory.embeddings_fallback import (
        decode_embedding,
        get_embedding_service,
        is_cached_embedding_valid,
        parse_dim_from_model_id,
        _warn_once,
    )
    _warn_once(__name__)

logger = logging.getLogger(__name__)


# Coarse-rank oversample factor — top-K = budget * this. The factor is
# the "confidence headroom" we give the LLM rerank: 3× means the
# semantic shortlist is 3× the budget so the LLM has 2 candidates per
# slot to choose from. Lower would over-trust cosine; higher would
# stuff the LLM prompt with more text than it can rank reliably.
from config import RECALL_COARSE_OVERSAMPLE as COARSE_OVERSAMPLE  # noqa: E402


class MemoryRecallReranker:
    """Stateless apart from the embedding service handle. Construct
    once per process and call ``aretrieve_candidates`` per recall."""

    def __init__(self) -> None:
        self._service = get_embedding_service()

    async def aretrieve_candidates(
        self,
        observations: list[dict],
        query_texts: list[str] | None,
        *,
        budget: int,
        config_manager,            # for the LLM rerank call
        rerank: bool = True,
    ) -> list[dict]:
        """Return up to ``budget`` candidate observations, ranked.

        ``observations`` is the union of persona + reflection rows the
        caller wants to consider, each carrying at least ``id``,
        ``text``, ``entity``, ``score`` (evidence_score, see
        ``memory.evidence.evidence_score``), and ideally an
        ``embedding`` triple.

        ``query_texts`` is the recall query in natural language —
        typically the texts of newly-extracted facts.  None or
        empty disables the cosine prefilter and falls back to
        evidence_score order.

        ``rerank=False`` skips the LLM call (used by tests and the
        fallback path) and returns the coarse-ranked top-``budget``
        directly.
        """
        if not observations:
            # 即使空召回也算一次 invoke，差值 = "想 recall 但没素材"，对
            # 评估 memory pipeline 健康度有意义。
            try:
                from utils.instrument import counter as _instr_counter
                _instr_counter("memory_recall_invoke", returned_empty=True)
            except Exception:
                # 埋点失败不能让 recall 路径报错 —— memory pipeline 是
                # response 关键路径，宁可少一条统计也不让它 crash。
                pass
            return []
        # Telemetry：每次 recall 算一次 invoke，无论后续是 coarse-only 还是
        # 走 LLM rerank。histogram 记返回 fact 数量分布；对比 invoke 次数和
        # 返回 0 的比例可看 memory pipeline 是否在"起作用"。
        try:
            from utils.instrument import counter as _instr_counter
            _instr_counter("memory_recall_invoke", returned_empty=False)
        except Exception:
            # 同上：埋点失败静默，不影响 recall 主路径。
            pass

        # Normalise query_texts up-front so phase 2 (coarse) and phase 3
        # (LLM rerank) see the same shape: drop None/empty/whitespace
        # entries, collapse to None when nothing remains. Without this,
        # callers passing [""] / ["   "] would get coarse-rank fallback
        # to evidence order (because _coarse_rank filters empties before
        # embed_batch) but still pay an LLM rerank with an empty
        # {QUERY} placeholder — wasted tokens and unstable output.
        if query_texts:
            query_texts = [t.strip() for t in query_texts if isinstance(t, str) and t.strip()]
            if not query_texts:
                query_texts = None

        # Phase 1: hard filter (suppressed / terminal / score < 0 /
        # protected).  This is the only stage that uses evidence_score
        # *directly* — everything else treats it as auxiliary signal.
        survivors = self._hard_filter(observations)
        if not survivors:
            return []

        # Phase 2: coarse rank.
        coarse_pool_size = budget * COARSE_OVERSAMPLE
        coarse = await self._coarse_rank(
            survivors, query_texts, k=coarse_pool_size,
        )
        if len(coarse) <= budget or not rerank:
            return coarse[:budget]

        # When the embedding service isn't available, the coarse step
        # already produced an evidence_score-DESC ordering — there's
        # no semantic basis to rerank further, so don't pay the LLM
        # cost. Mirrors the design intent: "vectors disabled ⇒
        # entirely the legacy stage 1/2 path".
        if not self._service.is_available():
            return coarse[:budget]

        # Phase 3: LLM rerank — only fires when we have enough
        # candidates that picking the best `budget` actually involves
        # judgment. Below the budget, every candidate makes the cut
        # anyway, so the LLM call would be wasted tokens.
        try:
            from config.prompts.prompts_memory import get_memory_recall_rerank_prompt
            from utils.language_utils import get_global_language
        except ImportError:
            # Prompt not available yet — degrade to coarse rank.
            return coarse[:budget]

        if not query_texts:
            # No queries → no semantic basis for an LLM rerank;
            # coarse order (which is also evidence_score order in
            # this branch) is the best we can do.
            return coarse[:budget]

        try:
            ranked = await self._fine_rank(
                coarse, query_texts, budget=budget,
                config_manager=config_manager,
                lang=get_global_language(),
                prompt_loader=get_memory_recall_rerank_prompt,
            )
        except Exception as e:  # noqa: BLE001 — best-effort, never crash recall
            logger.warning(
                "[MemoryRecall] LLM rerank failed (%s: %s); "
                "falling back to coarse rank order",
                type(e).__name__, e,
            )
            ranked = coarse[:budget]
        return ranked

    # ── phase 1: hard filter ─────────────────────────────────────────

    # Reflection statuses we drop from the rerank pool.  Note that
    # ``promoted`` is NOT here even though it lives in
    # `memory.reflection.REFLECTION_TERMINAL_STATUSES`: the upstream
    # `_aload_signal_targets` deliberately ships ``confirmed +
    # promoted`` as Stage-2 observation candidates (a promoted
    # reflection is the strongest signal we have — confirmed,
    # consolidated into persona, and still active).  Filtering it out
    # here would silently shrink the pool below what the legacy path
    # produced (CodeRabbit PR-957 Major).
    _REFLECTION_DROP_STATUSES = frozenset({
        'denied', 'archived', 'merged', 'promote_blocked',
    })

    @staticmethod
    def _hard_filter(observations: list[dict]) -> list[dict]:
        """Drop everything we know up-front shouldn't reach the LLM:

          * ``score < 0`` — evidence net-negative; user has been
            disputing this more than confirming it.
          * suppressed — explicit "AI is over-mentioning this" gate.
          * truly-dead reflection (denied / archived / merged /
            promote_blocked) — these are dead-letter or already
            consumed, can't generate fresh signals.  ``promoted`` is
            kept because the upstream pool includes it.
          * protected persona — character-card source; evidence is
            effectively infinite there, no signal would ever flip it.

        These are the same exclusions the render path already uses,
        consolidated into one function so callers don't duplicate the
        list.

        Per-entry try/except: observations usually come from JSON files
        (facts.json / reflections.json / persona.json) across a serialize/
        deserialize boundary. In theory we wrote them ourselves so the shape
        should be right, but manual edits / legacy leftovers / migration bugs
        can make ``text`` a list/int (breaking ``.strip()``) or ``score`` a
        string (breaking the ``< 0`` comparison). A single bad row must not
        take the whole filter down — both existing callers (hybrid_recall and
        Stage-2 signal detection) benefit. Codex review on PR #1385.
        """
        out: list[dict] = []
        for o in observations:
            if not isinstance(o, dict):
                continue
            try:
                score = o.get('score')
                if score is not None and score < 0:
                    continue
                if o.get('suppress') or o.get('suppressed'):
                    continue
                if o.get('protected'):
                    continue
                target_type = o.get('target_type')
                status = o.get('status')
                if (target_type == 'reflection'
                        and status in MemoryRecallReranker._REFLECTION_DROP_STATUSES):
                    continue
                text = o.get('text', '')
                if not text or not text.strip():
                    continue
            except (TypeError, AttributeError) as exc:
                # 单条 entry 字段类型不对（list/int/etc.）→ skip 这一行，
                # 继续过滤其余。仅 log 到 DEBUG 不刷屏（malformed 通常会
                # 连续命中很多条）。
                logger.debug(
                    "MemoryRecallReranker._hard_filter: skipping malformed "
                    "entry id=%r: %s: %s",
                    o.get('id'), type(exc).__name__, exc,
                )
                continue
            out.append(o)
        return out

    # ── phase 2: coarse rank by cosine ───────────────────────────────

    async def _coarse_rank(
        self,
        observations: list[dict],
        query_texts: list[str] | None,
        *,
        k: int,
    ) -> list[dict]:
        """Cosine top-K against ``query_texts``. Falls through to
        evidence_score order when vectors aren't usable.

        Each candidate is scored as the *max* cosine across all query
        texts (``query_text`` represents "things the user just
        mentioned"; we want the candidate to be relevant to ANY of
        them, not the average across all). max-pool also matches the
        single-query case trivially.

        Candidates without a usable embedding (worker hasn't reached
        them yet, or model_id mismatch) are kept in the coarse pool
        but with cosine = 0 — they fall through to the LLM rerank
        below the cosine-ranked rows. Better than dropping them
        entirely, since they may still be the right answer if the LLM
        can match on text alone.
        """
        # Default: evidence_score order (the legacy behaviour).
        evidence_sorted = sorted(
            observations, key=lambda o: o.get('score', 0.0), reverse=True,
        )
        if not query_texts:
            return evidence_sorted[:k]
        if not self._service.is_available():
            return evidence_sorted[:k]
        model_id = self._service.model_id()
        if model_id is None:
            return evidence_sorted[:k]

        query_vectors = await self._service.embed_batch(
            [t for t in query_texts if t],
        )
        query_vectors = [v for v in query_vectors if v is not None]
        if not query_vectors:
            return evidence_sorted[:k]

        # Split into two pools so un-embedded candidates can't be
        # starved out by embedded ones at the [:k] truncation
        # (CodeRabbit PR-957 Major).  The original implementation
        # tagged un-embedded with cosine = -1.0, then sorted+sliced —
        # whenever there were ≥k embedded candidates, every
        # unembedded one fell off the cliff before reaching the LLM
        # rerank, even though the docstring promised they'd "fall
        # through to the LLM rerank below the cosine-ranked rows".
        #
        # Decoding strategy: build a stacked candidate matrix once and
        # multiply against the query matrix in a single numpy call.
        # The pre-int8 path used a per-pair Python cosine loop; for N
        # candidates × Q queries × D dims that grew as N·Q·D Python
        # ops. With base64+int8 storage we'd otherwise pay a base64
        # decode per pair too. Stacking amortises decode to one pass
        # and pushes the dot product into BLAS — at 5k entries × 256d
        # that drops the coarse rank from hundreds of ms to a few.
        # Derive target_dim from the running service's model_id rather
        # than from the first decoded candidate. Codex review PR #1147
        # P2: if the first valid-on-paper row decoded to the wrong
        # length, the old "first wins" rule would push every correctly
        # sized candidate to the unembedded pool and silently lose the
        # cosine ranking. model_id encodes dim by construction (see
        # build_model_id), so it's the authoritative source.
        # If the id is unparseable (custom model_id from a fixture or
        # future profile), fall back to the first decoded row's dim —
        # better than dropping every candidate to unembedded.
        target_dim = parse_dim_from_model_id(model_id)

        embedded_obs: list[dict] = []
        embedded_decoded: list = []
        unembedded: list[dict] = []
        for o in observations:
            # ``observations`` already passed through ``_hard_filter``,
            # which guarantees every entry is a dict with non-empty
            # text — no need for a defensive isinstance check here.
            text = o.get('text', '')
            if not is_cached_embedding_valid(o, text, model_id):
                unembedded.append(o)
                continue
            cvec = decode_embedding(o.get('embedding'))
            # A row that passes is_cached_embedding_valid but fails to
            # decode (corrupt base64, or a future format we don't
            # know) falls through to the unembedded pool rather than
            # crashing the rerank.  is_cached_embedding_valid already
            # tries to decode and checks dim, so this branch is mainly
            # defence-in-depth for racey writes between validity check
            # and rerank.
            if cvec is None or cvec.size == 0:
                unembedded.append(o)
                continue
            if target_dim is None:
                target_dim = int(cvec.size)
            elif cvec.size != target_dim:
                # Mixed dims under a single model_id should be
                # impossible (model_id encodes dim), but defend
                # against it: drop to unembedded so the matmul stays
                # rectangular.
                unembedded.append(o)
                continue
            embedded_obs.append(o)
            embedded_decoded.append(cvec)

        embedded_scored: list[tuple[float, dict]] = []
        if embedded_decoded:
            import numpy as np
            candidate_matrix = np.stack(embedded_decoded)
            query_rows = []
            for qv in query_vectors:
                qvec = decode_embedding(qv)
                if qvec is not None and qvec.size == target_dim:
                    query_rows.append(qvec)
            if query_rows:
                query_matrix = np.stack(query_rows)
                # (N, D) @ (D, Q) → (N, Q); max across queries → (N,)
                scores_arr = (candidate_matrix @ query_matrix.T).max(axis=1)
                embedded_scored = list(
                    zip((float(s) for s in scores_arr), embedded_obs),
                )
            else:
                # All query vectors failed dim check — degrade to 0
                # cosine for every embedded candidate; evidence_score
                # tie-break still gives a deterministic order below.
                embedded_scored = [(0.0, o) for o in embedded_obs]
        # Sort embedded by cosine DESC, evidence_score DESC tie-break.
        embedded_scored.sort(
            key=lambda pair: (pair[0], pair[1].get('score', 0.0)),
            reverse=True,
        )
        embedded_ranked = [o for _, o in embedded_scored]
        # Sort unembedded by evidence_score DESC — the only signal we
        # have for them, and the legacy fallback for the whole pool
        # uses the same key.
        unembedded.sort(key=lambda o: o.get('score', 0.0), reverse=True)
        # Reserve up to UNEMBEDDED_QUOTA slots for unembedded entries
        # so the LLM rerank gets a chance to text-match them.  Fill
        # the rest with embedded entries (the cosine-ranked majority).
        unembed_quota = max(1, k // (COARSE_OVERSAMPLE + 1)) if unembedded else 0
        unembed_quota = min(unembed_quota, len(unembedded))
        embed_slot_count = max(0, k - unembed_quota)
        result = (
            embedded_ranked[:embed_slot_count]
            + unembedded[:unembed_quota]
        )
        # If the embedded pool was smaller than its allotment, top up
        # from the unembedded tail so we always emit min(k, total).
        deficit = k - len(result)
        if deficit > 0:
            tail = unembedded[unembed_quota:unembed_quota + deficit]
            result.extend(tail)
        return result[:k]

    # ── per-query top-K recall（reflection synthesis 用） ────────────────

    async def aretrieve_per_query_topk(
        self,
        observations: list[dict],
        query_texts: list[str],
        *,
        per_query_k: int,
        total_cap: int,
    ) -> list[dict]:
        """Per-query top-K cosine recall, union+dedup capped at ``total_cap``.

        Unlike the global max-pool top-K of ``aretrieve_candidates``: here
        **each query gets its own independent quota of ``per_query_k``**;
        results are merged with id dedup and truncated to ``total_cap``.

        Use case: reflection synthesis's ``{RELATED_CONTEXT_BLOCK}`` — when 20
        unabsorbed facts span scattered topics, max-pool lets hot topics crowd
        out cold ones; the per-query quota avoids that (decided in the PR #1401
        thread).

        Perf: one ``embed_batch`` packs all queries; candidates are decoded once
        into an (N, D) matrix, a single ``candidate_matrix @ query_matrix.T``
        yields the (N, Q) score matrix, and a per-column ``argpartition`` takes
        the top-K. Overall complexity O(N·D·Q + N·Q·log K); BLAS brings its own
        SIMD/multithreading — equivalent to "parallel" per-query calls without
        paying N round-trips to the embedding service.

        The fallback differs from the main path: embedding unavailable / no
        model_id / no candidate with a valid embedding → **return []
        directly**, with **no** degradation to evidence_score ordering.
        Rationale: the consumer (``_build_related_context_block``) injects the
        results into the LLM prompt as "semantic anchors"; a "high-score
        historical fact" with no real semantic link is attention pollution as
        an anchor — better an empty anchor.

        ⚠️ Code reuse: the candidate-matrix build / query decode largely mirror
        ``_coarse_rank``; we accept the small duplication for now and will
        extract a private ``_build_score_matrix`` helper once a third caller
        appears.
        """
        if not observations or not query_texts:
            return []

        survivors = self._hard_filter(observations)
        if not survivors:
            return []

        if not self._service.is_available():
            return []
        model_id = self._service.model_id()
        if model_id is None:
            return []

        cleaned_queries = [
            t.strip() for t in query_texts
            if isinstance(t, str) and t.strip()
        ]
        if not cleaned_queries:
            return []

        query_vectors_raw = await self._service.embed_batch(cleaned_queries)
        query_vectors_raw = [v for v in query_vectors_raw if v is not None]
        if not query_vectors_raw:
            return []

        target_dim = parse_dim_from_model_id(model_id)

        # Decode candidate embeddings once, build (N, D)
        indexed_decoded: list[tuple[int, "np.ndarray"]] = []
        for i, o in enumerate(survivors):
            text = o.get('text', '')
            if not is_cached_embedding_valid(o, text, model_id):
                continue
            cvec = decode_embedding(o.get('embedding'))
            if cvec is None or cvec.size == 0:
                continue
            if target_dim is None:
                target_dim = int(cvec.size)
            elif cvec.size != target_dim:
                continue
            indexed_decoded.append((i, cvec))

        if not indexed_decoded:
            return []

        import numpy as np
        candidate_matrix = np.stack([cvec for _, cvec in indexed_decoded])
        cand_survivor_indices = [i for i, _ in indexed_decoded]

        query_rows = []
        for qv in query_vectors_raw:
            qvec = decode_embedding(qv)
            if qvec is not None and qvec.size == target_dim:
                query_rows.append(qvec)
        if not query_rows:
            return []
        query_matrix = np.stack(query_rows)  # (Q, D)

        # (N, D) @ (D, Q) → (N, Q)
        scores_mat = candidate_matrix @ query_matrix.T

        n_candidates = scores_mat.shape[0]
        effective_k = max(0, min(per_query_k, n_candidates))
        if effective_k == 0 or total_cap <= 0:
            return []

        # Stage A：先把每条 query 的 top-K 候选 doc index 列表算出来——只算不截断，
        # 不参与 cap 决策（cap 留给 stage B round-robin 时统一执行）。
        per_query_picks: list[list[int]] = []  # 每元素是 list[survivor_idx]
        for q_idx in range(scores_mat.shape[1]):
            col = scores_mat[:, q_idx]
            if effective_k >= n_candidates:
                top_idx = np.argsort(-col)
            else:
                # argpartition O(N) 拿无序 top-K，再对这 K 个 argsort
                unsorted_top = np.argpartition(-col, effective_k - 1)[:effective_k]
                top_idx = unsorted_top[np.argsort(-col[unsorted_top])]
            per_query_picks.append([cand_survivor_indices[int(i)] for i in top_idx])

        # Stage B：round-robin 取每轮每个 query 的第 r 名 → dedup 入池 → 满 cap
        # 退出。fairness 关键点：cap 截断**只能发生在 round-robin 之后**，绝不
        # 在 per-query 内部 early-return——否则前几个 query 会吃光全部 slot、
        # 后面 query 一条 anchor 都拿不到，退化成 max-pool 那种 cold-topic
        # 饥饿（PR #1401 thread 用户原话："必须最后统一去 cap，不然便宜了先
        # 判定的 fact"）。
        #
        # round-robin 之后 dedup 入池的 ordering（query 内 #1 → query 间 #1 →
        # query 内 #2 → ...）也跟"first-seen = query 顺序 × within-query rank"
        # 的老语义不一样：现在的 ordering 是 "每条 query 的 #1 先于任何 query
        # 的 #2"，更贴近 prompt 里"每条 unabsorbed 平等享有 anchor"的设计意图。
        seen_ids: set[str] = set()
        result: list[dict] = []
        for rank in range(effective_k):
            for picks in per_query_picks:
                if rank >= len(picks):
                    continue
                doc = survivors[picks[rank]]
                doc_id = doc.get('id')
                if not doc_id or doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)
                result.append(doc)
                if len(result) >= total_cap:
                    return result

        return result

    # ── phase 3: LLM rerank ──────────────────────────────────────────

    async def _fine_rank(
        self,
        candidates: list[dict],
        query_texts: list[str],
        *,
        budget: int,
        config_manager,
        lang: str,
        prompt_loader,
    ) -> list[dict]:
        """Single LLM call: rerank ``candidates`` against
        ``query_texts``, return up to ``budget`` items in the LLM's
        preferred order.

        ``evidence_score`` is included in the prompt as a parenthetical
        annotation ("score=N") so the LLM can use it as auxiliary
        signal (a confirmed-many-times observation deserves slight
        priority over a fresh one), but the math no longer mixes
        evidence into a single rank — the dimension mismatch with
        cosine and the inability to encode "user has explicitly
        suppressed this" both make a linear blend wrong.
        """
        from utils.file_utils import robust_json_loads
        from utils.token_tracker import set_call_type
        from utils.llm_client import create_chat_llm_async

        # The id-keyed indirection prevents the LLM from inventing
        # ids that aren't in the candidate set.
        from config import (
            RECALL_PER_CANDIDATE_MAX_TOKENS,
            RECALL_CANDIDATES_TOTAL_MAX_TOKENS,
        )
        from utils.tokenize import truncate_to_tokens
        cand_lines = []
        id_to_obs: dict[str, dict] = {}
        for c in candidates:
            cid = c.get('id') or c.get('raw_id')
            if not cid:
                continue
            id_to_obs[cid] = c
            score = c.get('score', 0.0)
            # 单条 candidate text 截断到 RECALL_PER_CANDIDATE_MAX_TOKENS
            txt = truncate_to_tokens(c.get('text', '') or '', RECALL_PER_CANDIDATE_MAX_TOKENS)
            cand_lines.append(f"[{cid}] (score={score:.2f}) {txt}")
        if not cand_lines:
            return []

        query_text = "\n".join(f"- {q}" for q in query_texts if q)
        # 兜底总和截断（候选已 ranked，截尾的是低 score 的）
        candidates_text = truncate_to_tokens(
            "\n".join(cand_lines), RECALL_CANDIDATES_TOTAL_MAX_TOKENS
        )
        prompt = (
            prompt_loader(lang)
            .replace('{QUERY}', query_text)
            .replace('{CANDIDATES}', candidates_text)
            .replace('{BUDGET}', str(budget))
        )

        set_call_type("memory_recall_rerank")
        api_config = config_manager.get_model_api_config('summary')
        # timeout=8: recall 在 query_memory 请求路径上，上游 plugin/core/context.py
        # 默认 5s 截断；本地 8s 给 connect + 一次失败裕度。超时即抛
        # APITimeoutError，外层 try/except 已会降级到 coarse rank。
        # max_retries=0: 禁 SDK 自动重试，超时直接降级。
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        llm = await create_chat_llm_async(
            api_config['model'],
            api_config['base_url'], api_config['api_key'],
            provider_type=api_config.get('provider_type'),
            timeout=8, max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so the rerank-decisions array isn't truncated
        )
        try:
            resp = await llm.ainvoke(prompt)
        finally:
            await llm.aclose()
        raw = resp.content.strip()
        if raw.startswith("```"):
            # Case-insensitive strip handles ``` / ```json / ```JSON /
            # ```Json — small models occasionally emit non-canonical
            # casing or trailing newline immediately after the fence
            # tag, which the previous literal `.replace("```json", "")`
            # would miss.
            raw = re.sub(r'^```\w*\s*\n?', '', raw, count=1)
            raw = re.sub(r'\n?\s*```\s*$', '', raw, count=1)
            raw = raw.strip()
        decisions = robust_json_loads(raw)
        if not isinstance(decisions, list):
            logger.warning(
                "[MemoryRecall] rerank returned non-list (%s); falling back",
                type(decisions).__name__,
            )
            return candidates[:budget]

        # Pull ids in order, drop any the LLM hallucinated, cap at
        # budget. If the LLM returned fewer than budget, top up from
        # the coarse-rank tail so the caller still gets `budget` rows
        # whenever there were that many candidates.
        ranked: list[dict] = []
        seen: set[str] = set()
        for d in decisions:
            if not isinstance(d, dict):
                continue
            cid = d.get('id')
            if not isinstance(cid, str) or cid in seen:
                continue
            obs = id_to_obs.get(cid)
            if obs is None:
                continue
            ranked.append(obs)
            seen.add(cid)
            if len(ranked) >= budget:
                break
        if len(ranked) < budget:
            for c in candidates:
                cid = c.get('id') or c.get('raw_id')
                if cid in seen or not cid:
                    continue
                ranked.append(c)
                seen.add(cid)
                if len(ranked) >= budget:
                    break
        return ranked
