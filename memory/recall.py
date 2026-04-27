# -*- coding: utf-8 -*-
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
tell ``主人喜欢猫`` from ``主人讨厌猫`` (≈0.78), and "semantically
near" entries that the user didn't actually mean to bring up trigger
false positives in Stage-2 signal detection (which would either
reinforce or negate the wrong observation).  Keeping the LLM as the
arbiter means we save *prompt tokens* (smaller candidate set in the
Stage-2 prompt), not *LLM calls* — RFC §3.4 stays unchanged in
shape.
"""
from __future__ import annotations

import logging
import re

from memory.embeddings import (
    cosine_similarity,
    get_embedding_service,
    is_cached_embedding_valid,
)

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
            return []

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
            from config.prompts_memory import get_memory_recall_rerank_prompt
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
        """
        out: list[dict] = []
        for o in observations:
            if not isinstance(o, dict):
                continue
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
        embedded_scored: list[tuple[float, dict]] = []
        unembedded: list[dict] = []
        for o in observations:
            # ``observations`` already passed through ``_hard_filter``,
            # which guarantees every entry is a dict with non-empty
            # text — no need for a defensive isinstance check here.
            text = o.get('text', '')
            cvec = o.get('embedding')
            if is_cached_embedding_valid(o, text, model_id):
                # Max-cosine across query vectors. Candidates with
                # equal cosine fall back to evidence_score for tie-
                # breaking — covered in the unit test.
                best = max(
                    cosine_similarity(cvec, qv) for qv in query_vectors
                )
                embedded_scored.append((best, o))
            else:
                unembedded.append(o)
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
        from utils.llm_client import create_chat_llm

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
        llm = create_chat_llm(
            api_config['model'],
            api_config['base_url'], api_config['api_key'],
            timeout=8, max_retries=0,
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
