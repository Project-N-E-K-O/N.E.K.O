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

"""Passive memory recall: memories a chat message brings in unasked.

Before a text reply, Main asks for memories related to what the user just
said (``/internal/memory/{name}/turn_context``) and shows the hits to that
reply's requests alone. Nobody asked for them, so the bars are far higher
than ``recall_memory``'s:

* **pool** - what the system prompt does not already hold: active facts
  (minus those written during the current session, whose conversation is
  still in context), archived facts (keywords only), reflections the persona
  render leaves out, and persona entries it leaves out. Private scope only.
* **rare term** - the message and a memory share a term of at least three
  CJK characters or four letters that at most two memories contain;
* **keywords and meaning** - a top BM25 hit whose cosine clears the dual bar;
  without a usable vector signal this becomes a very high keyword bar
  instead (most of the message's terms in the memory, near the BM25 top);
* **meaning alone** - up to the two best cosines, clearing the higher bar,
  the last one taken leading the next by the calibrated margin.

Vector bars are calibrated on the loaded model against small talk
(``utils.semantic_calibration``). Nothing is written; nothing about the
session is kept.
"""

from __future__ import annotations

import asyncio
import itertools
import time
import weakref
from datetime import datetime
from typing import Any

import numpy as np

from config import HYBRID_RECALL_BM25_THRESHOLD
from config.turn_context_settings import (
    TURN_CONTEXT_CALIBRATION_BASE_QUANTILE,
    TURN_CONTEXT_CALIBRATION_BATCH,
    TURN_CONTEXT_CALIBRATION_BATCH_PAUSE_SECONDS,
    TURN_CONTEXT_CALIBRATION_HIGH_QUANTILE,
    TURN_CONTEXT_CALIBRATION_MIN_SAMPLES,
    TURN_CONTEXT_CALIBRATION_PROBES,
    TURN_CONTEXT_DUAL_STEPS,
    TURN_CONTEXT_LEAD_FLOOR,
    TURN_CONTEXT_LEAD_STEPS,
    TURN_CONTEXT_MAX_ITEMS_PER_KIND,
    TURN_CONTEXT_MEMORY_BM25_MIN_COVERAGE,
    TURN_CONTEXT_MEMORY_EXTRA_STEPS,
    TURN_CONTEXT_MEMORY_FLOOR,
    TURN_CONTEXT_QUERY_MAX_CHARS,
    TURN_CONTEXT_RARE_TERM_MAX_DOCS,
    TURN_CONTEXT_RARE_TERM_MIN_CJK_CHARS,
    TURN_CONTEXT_RARE_TERM_MIN_LATIN_CHARS,
    TURN_CONTEXT_SEMANTIC_STEPS,
)
from utils.logger_config import get_module_logger
from utils.prompt_fence import fence_safe
from utils.semantic_calibration import (
    ProbeVectors,
    background_from_scores,
    leading_take,
    passive_thresholds,
    probe_best_scores,
)

logger = get_module_logger(__name__, "Memory")

# Keyword evidence counts only near the top of the BM25 order.
PASSIVE_BM25_TOP = 8
# Without vectors, the keyword-only bar also needs the hit among the top two
# and a message long enough for coverage to mean something.
FALLBACK_BM25_TOP = 2
FALLBACK_MIN_QUERY_TERMS = 6
# Time kept back from the budget for rendering once the vector is in.
RENDER_RESERVE_SECONDS = 0.1
# A request that waited out its predecessor starts retrieval only with at
# least this much budget left besides the render reserve; with less, Main
# would drop the result while the pass kept the slot from the next turn.
MIN_RETRIEVAL_SECONDS = 0.05
# Distinct rare terms a memory must share when no vector can confirm it.
RARE_TERMS_WITHOUT_VECTOR = 2

_probes = ProbeVectors()
# Backgrounds per (character, model, pool): the pool rarely changes between
# turns, and its probe-by-memory product is the costly part.
_backgrounds: dict[tuple[Any, ...], Any] = {}
MAX_BACKGROUNDS = 32

# One request per character does the work at a time. Main drops a request at
# its 1 s budget, but the server keeps computing (a worker thread cannot be
# cancelled), so messages sent in quick succession used to stack full BM25
# passes. Now a newer request marks the older one superseded and waits for
# it to finish; the older one stops at its next checkpoint.
_request_numbers = itertools.count(1)
_latest_request: dict[str, int] = {}
# The newest request's "finished" future per character, per event loop (a
# future belongs to its loop). Waiting on a future instead of acquiring a
# lock means a wait that times out never leaves anything held.
_in_flight: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Future]]" = (
    weakref.WeakKeyDictionary()
)


def _claim(lanlan_name: str) -> tuple[asyncio.Future | None, asyncio.Future]:
    """Register a new request: the previous one's future, and this one's."""
    loop = asyncio.get_running_loop()
    per_loop = _in_flight.get(loop)
    if per_loop is None:
        per_loop = _in_flight[loop] = {}
    previous = per_loop.get(lanlan_name)
    finished = loop.create_future()
    per_loop[lanlan_name] = finished
    return previous, finished


def _release(lanlan_name: str, finished: asyncio.Future) -> None:
    if not finished.done():
        finished.set_result(None)
    per_loop = _in_flight.get(finished.get_loop())
    if per_loop is not None and per_loop.get(lanlan_name) is finished:
        del per_loop[lanlan_name]


def _release_after(
    running: list[asyncio.Future], lanlan_name: str, finished: asyncio.Future
) -> None:
    """Release ``finished`` only once everything in ``running`` is done.

    Work this request can no longer stop still holds the slot: the request
    it gave up waiting for (``busy``), or its own worker-thread pass when the
    request itself was cancelled (a thread runs to the end regardless). The
    next request waits for that work too instead of starting alongside it.
    """
    pending = [future for future in running if not future.done()]
    if not pending:
        _release(lanlan_name, finished)
        return
    # return_exceptions: the outcome is only waited on, and this also
    # consumes an error from a worker whose request is gone.
    waiter = asyncio.gather(*pending, return_exceptions=True)
    waiter.add_done_callback(lambda _done: _release(lanlan_name, finished))


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ" or "가" <= ch <= "힯"


def _is_rare_term_shape(term: str) -> bool:
    cjk = sum(1 for ch in term if _is_cjk(ch))
    if cjk:
        return cjk >= TURN_CONTEXT_RARE_TERM_MIN_CJK_CHARS
    return sum(1 for ch in term if ch.isalpha()) >= TURN_CONTEXT_RARE_TERM_MIN_LATIN_CHARS


def _ensure_probe_vectors(service, model_id: str) -> None:
    """Embed the probe sentences for ``model_id`` once, in the background."""
    _probes.ensure(
        model_id,
        service.embed_batch,
        TURN_CONTEXT_CALIBRATION_PROBES,
        batch=TURN_CONTEXT_CALIBRATION_BATCH,
        pause=TURN_CONTEXT_CALIBRATION_BATCH_PAUSE_SECONDS,
        min_samples=TURN_CONTEXT_CALIBRATION_MIN_SAMPLES,
    )


_common_terms_cache: list[set[str]] = []


def _common_terms() -> set[str]:
    """Terms of everyday small talk (the calibration probes): never rare,
    however seldom a memory pool happens to contain them."""
    if not _common_terms_cache:
        from memory.hybrid_recall import _tokenize

        _common_terms_cache.append(set(_tokenize(" ".join(TURN_CONTEXT_CALIBRATION_PROBES), [])))
    return _common_terms_cache[0]


def _parse_time(value) -> datetime | None:
    from memory.temporal import _parse_iso_safe, to_naive_local

    return to_naive_local(_parse_iso_safe(value))


async def _aload_pools(
    lanlan_name: str, fact_store, reflection_engine, persona_manager,
    session_started_at: datetime | None,
):
    from memory.hybrid_recall import (
        _aload_archive_facts,
        _aload_reflections_for_recall,
        _drop_archive_overlap,
        _render_reflection_split,
        _tag_tier,
    )
    from memory.recall import MemoryRecallReranker
    from memory.scopes import filter_entries_for_subjects

    facts, reflections, archive = await asyncio.gather(
        fact_store.aload_facts(lanlan_name),
        _aload_reflections_for_recall(reflection_engine, lanlan_name),
        _aload_archive_facts(fact_store, lanlan_name),
    )
    # Pending/confirmed come from the same cached parse, not two more reads.
    pending, confirmed = _render_reflection_split(reflections or [])
    facts = facts or []
    archive = _drop_archive_overlap(archive or [], facts, lanlan_name)
    persona_left_out: list[dict] = []
    resident_reflections: set[str] = set()
    if persona_manager is not None:
        persona_left_out, resident_reflections = await persona_manager.arecall_residency(
            lanlan_name, pending, confirmed,
        )
    if session_started_at is not None:
        # Facts written during this session come from a conversation the
        # model still has in front of it.
        facts = [
            fact for fact in facts
            if (created := _parse_time(fact.get('created_at'))) is None
            or created < session_started_at
        ]
    reflections = [
        reflection for reflection in reflections or []
        if str(reflection.get('id') or '') not in resident_reflections
    ]
    tagged = {
        'fact': _tag_tier(facts, 'fact'),
        'reflection': _tag_tier(reflections, 'reflection'),
        'fact_archive': _tag_tier(archive, 'fact_archive'),
        'persona': _tag_tier(persona_left_out, 'persona'),
    }
    tagged = {
        tier: filter_entries_for_subjects(rows, None, include_legacy_private=None)
        for tier, rows in tagged.items()
    }
    bm25_pool = MemoryRecallReranker._hard_filter(
        tagged['fact'] + tagged['reflection'] + tagged['fact_archive'] + tagged['persona']
    )
    embedding_pool = MemoryRecallReranker._hard_filter(
        tagged['fact'] + tagged['reflection'] + tagged['persona']
    )
    return bm25_pool, embedding_pool


def _pool_matrix(pool: list[dict], model_id: str, dim: int):
    """Unit vectors of the docs with a valid cached embedding, and those docs."""
    from memory.hybrid_recall import _cached_doc_vector, _ScanState

    rows: list[dict] = []
    vecs: list[np.ndarray] = []
    state = _ScanState()
    for doc in pool:
        cvec, cnorm = _cached_doc_vector(doc, doc.get('text', '') or '', model_id, state)
        if cvec is None or cvec.size != dim or cnorm <= 0:
            continue
        rows.append(doc)
        vecs.append(np.asarray(cvec, dtype=np.float32) / cnorm)
    if not rows:
        return None, []
    return np.vstack(vecs), rows


def _score(matrix: np.ndarray, query_vector: np.ndarray, probes: np.ndarray | None, cache_key):
    """Cosines of the query against the pool, and the pool's background
    (None without probes). Runs in a worker thread: on a large pool both are
    matrix products the event loop must not wait on."""
    sims = matrix @ query_vector
    if probes is None or probes.shape[1] != matrix.shape[1]:
        return sims, None
    if cache_key in _backgrounds:
        return sims, _backgrounds[cache_key]
    background = background_from_scores(
        probe_best_scores(probes, matrix),
        high_quantile=TURN_CONTEXT_CALIBRATION_HIGH_QUANTILE,
        base_quantile=TURN_CONTEXT_CALIBRATION_BASE_QUANTILE,
        min_samples=TURN_CONTEXT_CALIBRATION_MIN_SAMPLES,
    )
    if len(_backgrounds) >= MAX_BACKGROUNDS:
        _backgrounds.clear()
    _backgrounds[cache_key] = background
    return sims, background


def _render(picked: list[dict], lang: str) -> tuple[str, int]:
    from memory.recall_render import render_recall_block
    from utils.tokenize import count_tokens

    block = render_recall_block(
        [
            {
                "text": doc.get('text') or '',
                "tier": doc.get('_tier'),
                "entity": doc.get('entity'),
                "created_at": doc.get('created_at'),
                "event_start_at": doc.get('event_start_at'),
                "event_end_at": doc.get('event_end_at'),
            }
            for doc in picked
        ],
        lang,
    )
    text = fence_safe(block.text).strip()
    return text, (count_tokens(text) if text else 0)


async def amemory_turn_context(
    *,
    lanlan_name: str,
    text: str,
    fact_store,
    reflection_engine,
    persona_manager,
    config_manager,
    budget_ms: int,
    language: str,
    session_started_at: datetime | None = None,
) -> dict[str, Any]:
    """Memories to show this reply unasked; never raises.

    Returns ``{"result", "context", "memory_ids", "tokens", "retrieval_mode",
    "elapsed_ms"}``; ``context`` is the rendered lines, without a fence.
    """
    started = time.monotonic()
    deadline = started + max(int(budget_ms or 0), 50) / 1000
    query = str(text or "").strip()[:TURN_CONTEXT_QUERY_MAX_CHARS]

    def finish(result: str, *, picked=(), context: str = "", tokens: int = 0, mode: str = ""):
        elapsed = int((time.monotonic() - started) * 1000)
        logger.info(
            "[turn_context] %s: memory result=%s mode=%s hits=%d %dms",
            lanlan_name, result, mode, len(picked), elapsed,
        )
        return {
            "result": result,
            "context": context,
            "memory_ids": [str(doc.get('id') or '') for doc in picked],
            "tokens": tokens,
            "retrieval_mode": mode,
            "elapsed_ms": elapsed,
        }

    if not query:
        return finish("miss")
    number = next(_request_numbers)
    _latest_request[lanlan_name] = number
    previous, finished = _claim(lanlan_name)
    workers: list[asyncio.Future] = []

    async def in_worker(fn, /, *args, **kwargs):
        """``asyncio.to_thread``, tracked: a cancelled request keeps its slot
        until the thread it started has finished."""
        work = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
        workers.append(work)
        return await asyncio.shield(work)

    try:
        if previous is not None and not previous.done():
            # The request still running is superseded now and stops at its
            # next checkpoint; wait for that within this request's budget.
            remaining = deadline - time.monotonic() - RENDER_RESERVE_SECONDS
            if remaining > 0:
                await asyncio.wait({previous}, timeout=remaining)
            if not previous.done():
                return finish("busy")
            if deadline - time.monotonic() - RENDER_RESERVE_SECONDS < MIN_RETRIEVAL_SECONDS:
                return finish("busy")
        if _latest_request.get(lanlan_name) != number:
            return finish("superseded")
        return await _arecall(
            lanlan_name=lanlan_name,
            query=query,
            fact_store=fact_store,
            reflection_engine=reflection_engine,
            persona_manager=persona_manager,
            config_manager=config_manager,
            deadline=deadline,
            language=language,
            session_started_at=session_started_at,
            superseded=lambda: _latest_request.get(lanlan_name) != number,
            finish=finish,
            in_worker=in_worker,
            track=workers.append,
        )
    finally:
        # Gave up waiting (busy), or cancelled mid-pass: the slot stays taken
        # until the request still running, and this one's own threads and
        # cancelled embedding, end.
        running = [future for future in (previous, *workers) if future is not None]
        _release_after(running, lanlan_name, finished)


async def _arecall(
    *,
    lanlan_name: str,
    query: str,
    fact_store,
    reflection_engine,
    persona_manager,
    config_manager,
    deadline: float,
    language: str,
    session_started_at: datetime | None,
    superseded,
    finish,
    in_worker,
    track,
) -> dict[str, Any]:
    """The body of ``amemory_turn_context``, once the previous request is done.

    ``superseded()`` turns true once a newer request for the same character
    has arrived: Main has already dropped this one, so it stops at the next
    checkpoint instead of finishing work nobody will read. Thread work goes
    through ``in_worker``, and the query embedding is passed to ``track``, so
    the slot outlives whatever native work a cancel cannot stop (the embedding
    service keeps a cancelled inference task alive until its thread exits).
    """
    embed_task: asyncio.Task[Any] | None = None
    try:
        from memory.embeddings import get_embedding_service, parse_dim_from_model_id
        from memory.hybrid_recall import _bm25_scores, _tokenize

        service = get_embedding_service()
        model_id = service.model_id() if service.is_available() else None
        if model_id:
            _ensure_probe_vectors(service, model_id)
            embed_task = asyncio.create_task(service.embed_batch([query]))
            track(embed_task)
            # An early exit cancels it (see the finally below); this only
            # consumes the outcome nobody reads.
            embed_task.add_done_callback(lambda task: task.cancelled() or task.exception())

        # Tracked like the worker steps: a cold load parses files in threads
        # that a cancel cannot stop, so the slot waits for it.
        pools = asyncio.ensure_future(_aload_pools(
            lanlan_name, fact_store, reflection_engine, persona_manager, session_started_at,
        ))
        track(pools)
        bm25_pool, embedding_pool = await asyncio.shield(pools)
        if not bm25_pool:
            return finish("miss")
        if superseded():
            return finish("superseded")
        try:
            from memory.stop_names import collect_stop_names
            stop_names = collect_stop_names(config_manager) or []
        except Exception:  # noqa: BLE001
            stop_names = []

        bm25_ranked = await in_worker(_bm25_scores, query, bm25_pool, stop_names=stop_names)
        if superseded():
            return finish("superseded")
        bm25_scored = [(doc, score) for doc, score, _tf in bm25_ranked]
        query_terms = set(_tokenize(query, stop_names))
        # The query terms each doc holds, as BM25 already counted them.
        doc_terms = {id(doc): set(tf) for doc, _score, tf in bm25_ranked}
        document_frequency: dict[str, int] = {}
        for terms in doc_terms.values():
            for term in terms:
                document_frequency[term] = document_frequency.get(term, 0) + 1
        common = _common_terms()
        rare_terms = {
            term for term, count in document_frequency.items()
            if count <= TURN_CONTEXT_RARE_TERM_MAX_DOCS
            and _is_rare_term_shape(term)
            and term not in common
        }

        chosen: dict[int, tuple[int, float, dict]] = {}

        def offer(doc: dict, strength: int, score: float) -> None:
            current = chosen.get(id(doc))
            if current is None or (strength, score) > current[:2]:
                chosen[id(doc)] = (strength, score, doc)

        # A shared rare term is evidence of the same specific thing only when
        # something else agrees; it is judged once the vectors are in.
        rare_docs = [
            (doc, score, shared)
            for doc, score in bm25_scored
            if (shared := doc_terms.get(id(doc), set()) & rare_terms)
        ]

        query_vector: np.ndarray | None = None
        if embed_task is not None:
            remaining = deadline - time.monotonic() - RENDER_RESERVE_SECONDS
            done, _pending = await asyncio.wait({embed_task}, timeout=max(remaining, 0))
            if embed_task in done and embed_task.exception() is None:
                rows = embed_task.result() or []
                if rows and rows[0] is not None:
                    arr = np.asarray(rows[0], dtype=np.float32)
                    norm = float(np.linalg.norm(arr))
                    if norm > 0 and np.isfinite(norm):
                        query_vector = arr / norm
            else:
                embed_task.cancel()
        if superseded():
            return finish("superseded")

        thresholds = None
        cosine: list[tuple[dict, float]] = []
        if query_vector is not None and model_id:
            dim = parse_dim_from_model_id(model_id) or int(query_vector.size)
            matrix, rows = await in_worker(_pool_matrix, embedding_pool, model_id, dim)
            if superseded():
                return finish("superseded")
            if matrix is not None and matrix.shape[1] == query_vector.size:
                probes = _probes.get(model_id)
                # The same pool (ids and embedded texts) has the same background.
                cache_key = (
                    lanlan_name, model_id,
                    hash(tuple(
                        (doc.get('id'), doc.get('embedding_text_sha256')) for doc in rows
                    )),
                )
                sims, background = await in_worker(
                    _score, matrix, query_vector, probes, cache_key,
                )
                if superseded():
                    return finish("superseded")
                cosine = sorted(zip(rows, sims.tolist()), key=lambda pair: -pair[1])
                if background is not None:
                    thresholds = passive_thresholds(
                        background,
                        floor=TURN_CONTEXT_MEMORY_FLOOR,
                        dual_steps=TURN_CONTEXT_DUAL_STEPS,
                        semantic_steps=TURN_CONTEXT_SEMANTIC_STEPS,
                        extra_steps=TURN_CONTEXT_MEMORY_EXTRA_STEPS,
                        lead_steps=TURN_CONTEXT_LEAD_STEPS,
                        lead_floor=TURN_CONTEXT_LEAD_FLOOR,
                    )

        # Rare-term route: with calibrated vectors the memory must also clear
        # the dual cosine bar; a memory without a vector (or no calibration)
        # must share at least two distinct rare terms instead.
        cosine_by_doc = {id(doc): score for doc, score in cosine} if thresholds is not None else {}
        for doc, score, shared in rare_docs:
            cos = cosine_by_doc.get(id(doc))
            if (cos >= thresholds.dual) if cos is not None else len(shared) >= RARE_TERMS_WITHOUT_VECTOR:
                offer(doc, 3, score)

        bm25_top = [
            (doc, score) for doc, score in bm25_scored
            if score >= HYBRID_RECALL_BM25_THRESHOLD
        ][:PASSIVE_BM25_TOP]
        if thresholds is not None:
            mode = "hybrid"
            top_ids = {id(doc) for doc, _score in bm25_top}
            for doc, score in cosine:
                if id(doc) in top_ids and score >= thresholds.dual:
                    offer(doc, 2, score)
            take = leading_take(
                [score for _doc, score in cosine],
                threshold=thresholds.semantic,
                lead=thresholds.lead,
                max_take=TURN_CONTEXT_MAX_ITEMS_PER_KIND,
            )
            for doc, score in cosine[:take]:
                offer(doc, 1, score)
        else:
            # No usable vector signal: keywords alone, on a very high bar.
            mode = "bm25"
            if len(query_terms) >= FALLBACK_MIN_QUERY_TERMS:
                for doc, score in bm25_top[:FALLBACK_BM25_TOP]:
                    coverage = len(doc_terms.get(id(doc), set())) / len(query_terms)
                    if coverage >= TURN_CONTEXT_MEMORY_BM25_MIN_COVERAGE:
                        offer(doc, 2, score)

        ranked = sorted(chosen.values(), key=lambda item: (-item[0], -item[1]))
        picked = [doc for _strength, _score, doc in ranked[:TURN_CONTEXT_MAX_ITEMS_PER_KIND]]
        if not picked:
            return finish("miss", mode=mode)
        if superseded():
            return finish("superseded")
        context, tokens = await in_worker(_render, picked, language)
        if not context:
            return finish("miss", mode=mode)
        return finish("matched", picked=picked, context=context, tokens=tokens, mode=mode)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[turn_context] %s: memory turn context failed: %s", lanlan_name, type(exc).__name__,
            exc_info=True,
        )
        return finish("error")
    finally:
        # Every early exit leaves the query embedding unread.
        if embed_task is not None and not embed_task.done():
            embed_task.cancel()
