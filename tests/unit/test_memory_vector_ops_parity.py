"""Old-vs-new parity for the vectorized memory matrix paths.

Each optimized path is checked against a verbatim copy of the loop it
replaced, on random data that deliberately contains exactly equal scores:
the old loops used Python's stable sort, so equal scores keep ascending
index order, and the new code has to reproduce that order, not just the set.
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import memory._embeddings.ranking as ranking
from memory._embeddings.schema import stamp_embedding_fields

import memory.embeddings as emb
import memory.hybrid_recall as hr

MODEL_ID = "parity-profile-8d-fp32"
DIM = 8


# ── verbatim copies of the replaced loops ───────────────────────────────


def _legacy_threshold_top_k(row, threshold, topk):
    n = len(row)
    cand = [(int(j), float(row[j])) for j in range(n) if float(row[j]) >= threshold]
    cand.sort(key=lambda x: -x[1])
    return [j for j, _ in cand[:topk]]


def _legacy_clusters(matrix, threshold, topk, size_max):
    """``_compute_clusters`` before this change, from the stacked matrix on."""
    sim_matrix = matrix @ matrix.T
    np.fill_diagonal(sim_matrix, -1.0)
    n = matrix.shape[0]
    adj = [[] for _ in range(n)]
    for i in range(n):
        adj[i] = _legacy_threshold_top_k(sim_matrix[i], threshold, topk)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in adj[i]:
            union(i, j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    clusters = []
    for indices in groups.values():
        if len(indices) < 2:
            continue
        if len(indices) > size_max:
            strengths = []
            for i in indices:
                others = [j for j in indices if j != i]
                max_sim = max((float(sim_matrix[i][j]) for j in others), default=-1.0)
                strengths.append((i, max_sim))
            strengths.sort(key=lambda x: -x[1])
            indices = [s[0] for s in strengths[:size_max]]
        clusters.append(indices)
    return clusters


def _legacy_decode(entries, model_id, target_dim):
    """The recall reranker's old decode loop: validity check, then decode again."""
    out = []
    for e in entries:
        text = e.get('text', '')
        if not emb.is_cached_embedding_valid(e, text, model_id):
            out.append(None)
            continue
        cvec = emb.decode_embedding(e.get('embedding'))
        if cvec is None or cvec.size == 0:
            out.append(None)
            continue
        if target_dim is None:
            target_dim = int(cvec.size)
        elif cvec.size != target_dim:
            out.append(None)
            continue
        out.append(cvec)
    return out, target_dim


# ── data with ties ──────────────────────────────────────────────────────


def _discrete_vectors(rng, n, dim=DIM):
    """Entries in {-1, 0, 1}: dot products are small integers, so many
    similarities are exactly equal and exactly representable."""
    return rng.integers(-1, 2, size=(n, dim)).astype(np.float32)


def _unit_vectors(rng, n, dim=DIM, duplicates=0):
    vecs = rng.standard_normal((n, dim)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    for _ in range(duplicates):
        a, b = rng.integers(0, n, size=2)
        vecs[b] = vecs[a]
    return vecs


def _threshold_rounding_down():
    """A threshold whose float32 rounding lands below it (the case where a
    bare ``float32_row >= threshold`` disagrees with the old Python compare)."""
    for t in np.arange(0.80, 0.99, 0.001):
        t = float(round(t, 3))
        if float(np.float32(t)) < t:
            return t
    raise AssertionError("no rounding-down threshold found")


# ── stable_top_indices / threshold_top_k ────────────────────────────────


@pytest.mark.parametrize("seed", range(20))
def test_stable_top_indices_matches_stable_sort(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(1, 60))
    values = rng.integers(-3, 4, size=n).astype(np.float32)  # heavy ties
    expected_full = sorted(range(n), key=lambda i: -float(values[i]))
    assert ranking.stable_top_indices(values).tolist() == expected_full
    for k in range(0, n + 3):
        assert ranking.stable_top_indices(values, k).tolist() == expected_full[:k], k


def test_threshold_top_k_rounding_down_threshold_is_exact():
    t = _threshold_rounding_down()
    at = np.float32(t)  # strictly below t as a Python float
    above = np.nextafter(at, np.float32(np.inf))
    row = np.array([at, above, 0.0, at, above], dtype=np.float32)
    assert _legacy_threshold_top_k(row, t, 5) == [1, 4]
    assert ranking.threshold_top_k(row, t, 5) == [1, 4]
    # The naive float32 comparison is the bug this guards against.
    assert np.flatnonzero(row >= t).tolist() != [1, 4]


@pytest.mark.parametrize("seed", range(30))
def test_threshold_top_k_matches_legacy_with_ties(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(1, 80))
    row = (rng.integers(-4, 5, size=n) / 4.0).astype(np.float32)
    for threshold in (-1.0, 0.0, 0.25, 0.5, 1.0, 2.0, _threshold_rounding_down()):
        for k in (-2, 0, 1, 2, 3, 5, n, n + 4):
            assert ranking.threshold_top_k(row, threshold, k) == _legacy_threshold_top_k(
                row, threshold, k,
            ), (threshold, k)


# ── cosine_clusters ─────────────────────────────────────────────────────


@pytest.mark.parametrize("seed", range(25))
def test_cosine_clusters_matches_legacy_on_discrete_ties(seed):
    rng = np.random.default_rng(seed)
    matrix = _discrete_vectors(rng, int(rng.integers(2, 90)))
    for threshold, topk, size_max in (
        (1.0, 2, 3), (2.0, 5, 8), (3.0, 1, 2), (0.0, 3, 4), (2.0, 5, 1000),
    ):
        assert ranking.cosine_clusters(
            matrix, threshold=threshold, topk=topk, size_max=size_max,
        ) == _legacy_clusters(matrix, threshold, topk, size_max), (
            threshold, topk, size_max,
        )


@pytest.mark.parametrize("seed", range(10))
def test_cosine_clusters_matches_legacy_on_unit_vectors(seed):
    rng = np.random.default_rng(100 + seed)
    matrix = _unit_vectors(rng, 300, dim=4, duplicates=40)
    for threshold, topk, size_max in (
        (0.85, 5, 8), (_threshold_rounding_down(), 5, 8), (0.5, 3, 6), (0.95, 2, 4),
    ):
        assert ranking.cosine_clusters(
            matrix, threshold=threshold, topk=topk, size_max=size_max,
        ) == _legacy_clusters(matrix, threshold, topk, size_max)


def test_cosine_clusters_truncation_ties_keep_ascending_index():
    # Five identical vectors plus a weaker member: every identical member has
    # the same strongest neighbour, so truncation must keep the lowest indices.
    base = np.array([1, 1, 0, 0, 0, 0, 0, 0], dtype=np.float32)
    weak = np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32)
    matrix = np.stack([weak, base, base, base, base, base])
    expected = _legacy_clusters(matrix, 1.0, 5, 3)
    assert expected == [[1, 2, 3]]
    assert ranking.cosine_clusters(matrix, threshold=1.0, topk=5, size_max=3) == expected


# ── refine engines end to end ───────────────────────────────────────────


class _Service:
    def is_disabled(self):
        return False

    def is_available(self):
        return True

    def model_id(self):
        return MODEL_ID


def _stamped(eid, vec, text=None, model_id=MODEL_ID):
    text = text or f"entry {eid}"
    entry = {"id": eid, "text": text}
    stamp_embedding_fields(entry, np.asarray(vec, dtype=np.float32), text, model_id)
    return entry


def _refine_entries(seed):
    rng = np.random.default_rng(seed)
    vecs = _discrete_vectors(rng, 70)
    entries = [_stamped(f"e{i}", v) for i, v in enumerate(vecs)]
    # Rows the validity check must drop, scattered through the pool.
    entries.insert(5, {"id": "plain", "text": "no vector"})
    entries.insert(20, _stamped("other_model", vecs[0], model_id="parity-profile-8d-int8"))
    stale = _stamped("stale", vecs[1])
    stale["text"] = "edited after embedding"
    entries.insert(33, stale)
    entries.insert(40, _stamped("short", np.ones(4, dtype=np.float32)))
    return entries


def _legacy_entry_clusters(entries, threshold, topk, size_max):
    vecs, _ = _legacy_decode(entries, MODEL_ID, DIM)
    valid = [(e, v) for e, v in zip(entries, vecs) if v is not None]
    matrix = np.stack([v for _, v in valid])
    return [
        [valid[i][0]["id"] for i in group]
        for group in _legacy_clusters(matrix, threshold, topk, size_max)
    ]


@pytest.mark.parametrize("seed", range(6))
def test_refine_engine_clusters_match_legacy(seed):
    from config import (
        MEMORY_REFINE_CLUSTER_SIZE_MAX,
        MEMORY_REFINE_COSINE_THRESHOLD,
        MEMORY_REFINE_TOPK_PER_ENTRY,
    )
    from memory.refine import MemoryRefineEngine

    entries = _refine_entries(seed)
    with patch("memory.refine.get_embedding_service", return_value=_Service()), \
         patch("memory.refine.MEMORY_REFINE_COSINE_THRESHOLD", 2.0):
        engine = MemoryRefineEngine(MagicMock())
        got = [[e["id"] for e in c] for c in engine._compute_clusters(entries)]
    assert got == _legacy_entry_clusters(
        entries, 2.0, MEMORY_REFINE_TOPK_PER_ENTRY, MEMORY_REFINE_CLUSTER_SIZE_MAX,
    )
    assert got, "fixture should form clusters"
    assert MEMORY_REFINE_COSINE_THRESHOLD  # real constant still importable


@pytest.mark.parametrize("seed", range(6))
def test_scoped_refine_engine_clusters_match_legacy(seed):
    from config import SCOPED_REFINE_CLUSTER_SIZE_MAX, SCOPED_REFINE_TOPK_PER_ENTRY
    from memory.scoped_refine import ScopedLiteRefineEngine

    entries = _refine_entries(seed)
    with patch("memory.scoped_refine.get_embedding_service", return_value=_Service()), \
         patch("memory.scoped_refine.SCOPED_REFINE_COSINE_THRESHOLD", 2.0):
        engine = ScopedLiteRefineEngine(MagicMock())
        got = [[e["id"] for e in c] for c in engine._compute_clusters(entries)]
    assert got == _legacy_entry_clusters(
        entries, 2.0, SCOPED_REFINE_TOPK_PER_ENTRY, SCOPED_REFINE_CLUSTER_SIZE_MAX,
    )
    assert got


@pytest.mark.asyncio
async def test_clusters_run_off_loop_on_snapshots_and_return_originals():
    entries = [{"id": f"x{i}", "text": "t"} for i in range(4)]
    loop_thread = threading.get_ident()
    seen = {}

    def compute(snapshot):
        seen["thread"] = threading.get_ident()
        seen["copies"] = all(s is not e and s == e for s, e in zip(snapshot, entries))
        return [[snapshot[2], snapshot[0]], [snapshot[3], snapshot[1]]]

    clusters = await ranking.run_clusters_off_loop(compute, entries)
    assert seen["thread"] != loop_thread
    assert seen["copies"]
    assert clusters[0][0] is entries[2] and clusters[0][1] is entries[0]
    assert clusters[1][0] is entries[3] and clusters[1][1] is entries[1]


@pytest.mark.asyncio
async def test_refine_pass_clusters_off_the_event_loop():
    from memory.refine import MemoryRefineEngine

    with patch("memory.refine.get_embedding_service", return_value=_Service()):
        engine = MemoryRefineEngine(MagicMock())
    loop_thread = threading.get_ident()
    threads = []

    def fake_compute(entries):
        threads.append(threading.get_ident())
        return []

    engine._compute_clusters = fake_compute
    await engine.refine_pass({"master": [{"id": "a"}, {"id": "b"}]}, apply_fn=AsyncMock(), scope_label="t")
    assert threads and all(t != loop_thread for t in threads)


@pytest.mark.asyncio
async def test_scoped_refine_pass_clusters_off_the_event_loop():
    from memory.scoped_refine import ScopedLiteRefineEngine, ScopedRefineBucket

    with patch("memory.scoped_refine.get_embedding_service", return_value=_Service()):
        engine = ScopedLiteRefineEngine(MagicMock())
    loop_thread = threading.get_ident()
    threads = []

    def fake_compute(entries):
        threads.append(threading.get_ident())
        return []

    engine._compute_clusters = fake_compute
    bucket = MagicMock(spec=ScopedRefineBucket)
    bucket.marker = ("a", "b", "c")
    bucket.entries = [{"id": "a"}, {"id": "b"}]
    await engine.refine_pass([bucket], apply_fn=AsyncMock(), scope_label="t")
    assert threads and all(t != loop_thread for t in threads)


# ── decode through the shared cache (recall reranker) ───────────────────


@pytest.fixture
def fresh_vec_cache():
    hr._invalidate_vec_cache()
    yield
    hr._invalidate_vec_cache()


def _mixed_pool(seed):
    rng = np.random.default_rng(seed)
    pool = [_stamped(f"r{i}", v) for i, v in enumerate(_unit_vectors(rng, 40))]
    pool[3] = {"id": "plain", "text": "no vector"}
    pool[7] = _stamped("other_model", np.ones(DIM), model_id="parity-profile-8d-int8")
    pool[9]["text"] = "edited after embedding"
    pool[11]["embedding"] = "not base64!!"
    pool[13] = _stamped("short", np.ones(4, dtype=np.float32))
    del pool[15]["id"]  # valid vector, but uncacheable without an id
    return pool


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("target_dim", [None, DIM])
def test_decode_cached_vectors_matches_legacy_cold_and_warm(fresh_vec_cache, seed, target_dim):
    pool = _mixed_pool(seed)
    expected, expected_dim = _legacy_decode(pool, MODEL_ID, target_dim)
    for _round in ("cold", "warm"):
        got, norms, dim = hr.decode_cached_vectors(
            hr.embedding_views(pool, lambda o: o.get('text', '')), MODEL_ID, target_dim,
        )
        assert dim == expected_dim, _round
        assert [g is None for g in got] == [e is None for e in expected], _round
        for g, e, norm in zip(got, expected, norms):
            if e is not None:
                assert np.array_equal(g, e) and g.dtype == e.dtype, _round
                assert norm == float(np.linalg.norm(e)), _round


@pytest.mark.asyncio
async def test_recall_rerankers_decode_off_the_event_loop(fresh_vec_cache):
    from memory.recall import MemoryRecallReranker

    rng = np.random.default_rng(7)
    pool = [
        {**_stamped(f"r{i}", v), "score": float(i)}
        for i, v in enumerate(_unit_vectors(rng, 12))
    ]
    service = MagicMock()
    service.is_available = MagicMock(return_value=True)
    service.model_id = MagicMock(return_value=MODEL_ID)
    service.embed_batch = AsyncMock(return_value=[_unit_vectors(rng, 1)[0]])
    loop_thread = threading.get_ident()
    threads = []
    real = hr._cached_doc_vector

    def spy(*args, **kwargs):
        threads.append(threading.get_ident())
        return real(*args, **kwargs)

    reranker = MemoryRecallReranker.__new__(MemoryRecallReranker)
    reranker._service = service
    with patch.object(hr, "_cached_doc_vector", spy):
        coarse = await reranker._coarse_rank(pool, ["q"], k=5)
        per_query = await reranker.aretrieve_per_query_topk(pool, ["q"], per_query_k=3, total_cap=3)
    assert len(coarse) == 5 and len(per_query) == 3
    assert threads and all(t != loop_thread for t in threads)


# ── hybrid recall cosine head ───────────────────────────────────────────


def _legacy_cosine_full(pool, qarr):
    """``_cosine_rank``'s old scoring tail (rows, keep mask, full sort)."""
    qnorm = float(np.linalg.norm(qarr))
    rows, vecs, norms = [], [], []
    for doc in pool:
        cvec = emb.decode_valid_cached_embedding(doc, doc.get('text', '') or '', MODEL_ID)
        if cvec is None or cvec.size != DIM:
            continue
        rows.append(doc)
        vecs.append(cvec)
        norms.append(float(np.linalg.norm(cvec)))
    if not rows:
        return []
    mat = np.vstack(vecs).astype(np.float32, copy=False)
    norms = np.asarray(norms, dtype=np.float32)
    keep = norms > 0
    sims = (mat @ qarr) / (qnorm * np.where(keep, norms, 1.0))
    scored = [(doc, float(cos)) for doc, cos, ok in zip(rows, sims, keep) if ok]
    scored.sort(key=lambda p: p[1], reverse=True)
    return scored


def _cosine_pool(seed):
    rng = np.random.default_rng(seed)
    vecs = _discrete_vectors(rng, 120)  # many exactly-equal cosines
    pool = [_stamped(f"c{i}", v) for i, v in enumerate(vecs)]
    pool[4] = _stamped("zero", np.zeros(DIM))  # norm 0: masked out
    pool[8] = {"id": "plain", "text": "no vector"}
    return pool, rng.integers(-1, 2, size=DIM).astype(np.float32) + np.float32(0.5)


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(6))
async def test_cosine_rank_head_matches_legacy_full_sort(fresh_vec_cache, seed):
    pool, qarr = _cosine_pool(seed)
    expected = _legacy_cosine_full(pool, qarr)
    service = MagicMock()
    service.is_available = MagicMock(return_value=True)
    service.model_id = MagicMock(return_value=MODEL_ID)
    service.embed_batch = AsyncMock(return_value=[qarr])

    def ids(ranking):
        return [(d["id"], s) for d, s in ranking]

    with patch.object(emb, "get_embedding_service", MagicMock(return_value=service)):
        full = await hr._cosine_rank("q", pool)
        assert ids(full) == ids(expected)
        for limit in (0, 1, 2, 4, 7, len(expected), len(expected) + 5):
            stats = {}
            head = await hr._cosine_rank("q", pool, limit=limit, stats=stats)
            assert ids(head) == ids(expected)[:limit], limit
            assert stats["scored"] == len(expected)


@pytest.mark.asyncio
async def test_cosine_rank_scores_off_the_event_loop(fresh_vec_cache):
    pool, qarr = _cosine_pool(0)
    service = MagicMock()
    service.is_available = MagicMock(return_value=True)
    service.model_id = MagicMock(return_value=MODEL_ID)
    service.embed_batch = AsyncMock(return_value=[qarr])
    loop_thread = threading.get_ident()
    threads = []
    real = hr._cached_doc_vector

    def spy(*args, **kwargs):
        threads.append(threading.get_ident())
        return real(*args, **kwargs)

    with patch.object(emb, "get_embedding_service", MagicMock(return_value=service)), \
         patch.object(hr, "_cached_doc_vector", spy):
        assert await hr._cosine_rank("q", pool, limit=4)
    assert threads and all(t != loop_thread for t in threads)


# ── reflection candidate scoring ────────────────────────────────────────


def _legacy_reflection_scores(candidates, query_rows, target_dim):
    """``ascore_against_queries``' old candidate half, verbatim."""
    texts = [str(e.get('text') or '') for e in candidates]
    vectors = [
        emb.decode_valid_cached_embedding(e, text, MODEL_ID) if text else None
        for e, text in zip(candidates, texts)
    ]
    index, rows = [], []
    for i, v in enumerate(vectors):
        if v is None or v.size != target_dim:
            continue
        norm = float(np.linalg.norm(v))
        if not np.isfinite(norm) or norm <= 0:
            continue
        index.append(i)
        rows.append(v / norm)
    scores = [None] * len(candidates)
    if rows:
        matrix = np.stack(rows) @ np.stack(query_rows).T
        for slot, score in zip(index, matrix.max(axis=1)):
            scores[slot] = float(score)
    return scores


@pytest.mark.parametrize("seed", range(8))
def test_reflection_candidate_scores_match_legacy_exactly(fresh_vec_cache, seed):
    from memory.reflection.similarity import _score_candidates

    rng = np.random.default_rng(seed)
    # Not unit length, so the per-row normalization actually matters.
    raw = rng.standard_normal((200, DIM)).astype(np.float32) * 3
    candidates = [_stamped(f"p{i}", v) for i, v in enumerate(raw)]
    candidates[2] = {"id": "empty", "text": ""}
    candidates[6] = _stamped("zero", np.zeros(DIM))
    candidates[9]["text"] = "edited"
    candidates[12] = _stamped("short", np.ones(4), model_id=MODEL_ID)
    query_rows = list(_unit_vectors(rng, 3))
    expected = _legacy_reflection_scores(candidates, query_rows, DIM)
    for _round in ("cold", "warm"):
        got = _score_candidates(
            hr.embedding_views(candidates, lambda e: str(e.get('text') or '')),
            MODEL_ID, DIM, np.stack(query_rows),
        )
        assert got == expected, _round


@pytest.mark.asyncio
async def test_reflection_scoring_runs_off_the_event_loop(fresh_vec_cache):
    from memory.reflection.similarity import ascore_against_queries

    rng = np.random.default_rng(3)
    candidates = [_stamped(f"p{i}", v) for i, v in enumerate(_unit_vectors(rng, 10))]
    query = [_stamped("q", _unit_vectors(rng, 1)[0])]
    loop_thread = threading.get_ident()
    threads = []
    real = hr._cached_doc_vector

    def spy(*args, **kwargs):
        threads.append(threading.get_ident())
        return real(*args, **kwargs)

    service = _Service()
    service.embed_batch = AsyncMock(return_value=[])
    with patch("memory.embeddings.get_embedding_service", return_value=service), \
         patch.object(hr, "_cached_doc_vector", spy):
        scores = await ascore_against_queries(query, candidates)
    assert scores is not None and all(s is not None for s in scores)
    assert threads and all(t != loop_thread for t in threads)


# ── the shared cache under concurrent scans ─────────────────────────────


def test_vec_cache_byte_accounting_survives_concurrent_scans(fresh_vec_cache):
    rng = np.random.default_rng(11)
    pools = [
        [_stamped(f"t{t}_{i}", v) for i, v in enumerate(_unit_vectors(rng, 300))]
        for t in range(4)
    ]
    # Small enough that every scan evicts.
    errors = []
    admitted = []
    with patch.object(hr, "HYBRID_RECALL_VEC_CACHE_MAX_BYTES", 200_000):
        def scan(pool):
            try:
                for _ in range(5):
                    vectors, _norms, _dim = hr.decode_cached_vectors(
                        hr.embedding_views(pool, lambda o: o.get('text', '')), MODEL_ID, None,
                    )
                    admitted.append(sum(v is not None for v in vectors))
            except Exception as exc:  # surfaced below; a thread would swallow it
                errors.append(exc)

        threads = [threading.Thread(target=scan, args=(p,)) for p in pools]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert admitted == [300] * 20  # every scan ran and decoded its whole pool
        assert hr._VEC_CACHE, "the cache should hold something after the scans"
        assert hr._VEC_CACHE_BYTES == sum(entry[6] for entry in hr._VEC_CACHE.values())
        assert hr._VEC_CACHE_BYTES <= 200_000


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(4))
async def test_hybrid_recall_results_unchanged_by_cosine_head_cut(fresh_vec_cache, tmp_path, seed):
    """The call site asks ``_cosine_rank`` for only the head it reads; the
    fused recall must equal what the full ranking produced."""
    pool, qarr = _cosine_pool(seed)
    facts = [
        {**doc, "score": 1.0} for doc in pool
        if doc.get("embedding") is not None
    ]
    service = MagicMock()
    service.is_available = MagicMock(return_value=True)
    service.model_id = MagicMock(return_value=MODEL_ID)
    service.embed_batch = AsyncMock(return_value=[qarr])
    fact_store = MagicMock()
    fact_store.aload_facts = AsyncMock(return_value=facts)
    fact_store._facts_archive_path = MagicMock(return_value=str(tmp_path / "none.json"))
    reflection_engine = MagicMock()
    reflection_engine.aload_reflections = AsyncMock(return_value=[])

    real = hr._cosine_rank
    seen_limits = []

    async def full_ranking(query, rows, **kwargs):
        seen_limits.append(kwargs.get("limit"))
        kwargs["limit"] = None
        return await real(query, rows, **kwargs)

    async def run():
        hr._invalidate_pool_cache()
        return await hr.hybrid_recall(
            lanlan_name="parity", query="vector query", fact_store=fact_store,
            reflection_engine=reflection_engine, config_manager=MagicMock(),
        )

    with patch.object(emb, "get_embedding_service", MagicMock(return_value=service)), \
         patch.object(hr, "HYBRID_RECALL_COSINE_THRESHOLD", -1.0):
        cut = await run()
        with patch.object(hr, "_cosine_rank", full_ranking):
            full = await run()
    assert seen_limits == [hr.HYBRID_RECALL_BUDGET_EACH]
    assert [r["id"] for r in cut["results"]] == [r["id"] for r in full["results"]]
    assert cut["results"], "fixture should recall something"


def test_turn_context_pool_matrix_matches_per_row_normalization(fresh_vec_cache):
    from memory.turn_context import _pool_matrix

    rng = np.random.default_rng(5)
    raw = rng.standard_normal((150, DIM)).astype(np.float32) * 2.5
    pool = [_stamped(f"t{i}", v) for i, v in enumerate(raw)]
    pool[3] = _stamped("zero", np.zeros(DIM))
    pool[7] = {"id": "plain", "text": "no vector"}

    def legacy():
        rows, vecs = [], []
        state = hr._ScanState()
        for doc in pool:
            cvec, cnorm = hr._cached_doc_vector(doc, doc.get('text', '') or '', MODEL_ID, state)
            if cvec is None or cvec.size != DIM or cnorm <= 0:
                continue
            rows.append(doc)
            vecs.append(np.asarray(cvec, dtype=np.float32) / cnorm)
        return np.vstack(vecs), rows

    expected, expected_rows = legacy()
    for _round in ("cold", "warm"):
        if _round == "cold":
            hr._invalidate_vec_cache()
        matrix, rows = _pool_matrix(pool, MODEL_ID, DIM)
        assert rows == expected_rows
        assert matrix.dtype == expected.dtype and np.array_equal(matrix, expected)


def test_decode_cached_vectors_first_row_fixes_dim_without_parseable_model(fresh_vec_cache):
    """With a model id that carries no dimension, the first valid row fixes
    it and later rows of another size drop out — the old loop's rule."""
    model = "parity-custom-model"
    pool = []
    for i, size in enumerate([6, 6, 4, 6, 9]):
        text = f"row {i}"
        entry = {"id": f"d{i}", "text": text}
        stamp_embedding_fields(entry, np.arange(1, size + 1, dtype=np.float32), text, model)
        pool.append(entry)
    expected, expected_dim = _legacy_decode(pool, model, None)
    got, _norms, dim = hr.decode_cached_vectors(
        hr.embedding_views(pool, lambda o: o.get('text', '')), model, None,
    )
    assert dim == expected_dim == 6
    assert [g is None for g in got] == [e is None for e in expected] == [
        False, False, True, False, True,
    ]


def test_reflection_scores_skip_empty_text_even_with_a_valid_stamp(fresh_vec_cache):
    from memory.reflection.similarity import _score_candidates

    empty = {"id": "empty", "text": ""}
    stamp_embedding_fields(empty, np.ones(DIM, dtype=np.float32), "", MODEL_ID)
    candidates = [empty, _stamped("p1", np.ones(DIM))]
    query_rows = [np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)]
    expected = _legacy_reflection_scores(candidates, query_rows, DIM)
    assert expected[0] is None and expected[1] is not None
    got = _score_candidates(
        hr.embedding_views(candidates, lambda e: str(e.get('text') or '')),
        MODEL_ID, DIM, np.stack(query_rows),
    )
    assert got == expected


@pytest.mark.parametrize("block", [1, 3, 7])
def test_cosine_clusters_blocked_truncation_matches_legacy(block):
    """Truncation takes in-component maxima in row blocks; small blocks force
    many block boundaries inside one large component."""
    rng = np.random.default_rng(42)
    matrix = _discrete_vectors(rng, 80)
    with patch.object(ranking, "_ROW_BLOCK", block):
        got = ranking.cosine_clusters(matrix, threshold=1.0, topk=3, size_max=5)
    expected = _legacy_clusters(matrix, 1.0, 3, 5)
    assert got == expected
    assert any(len(c) == 5 for c in expected), "fixture should truncate"


@pytest.mark.parametrize("block", [1, 3, 7, 256])
def test_strongest_internal_matches_naive_max_over_others(block):
    rng = np.random.default_rng(9)
    sim = rng.standard_normal((60, 60)).astype(np.float32)  # no ties
    np.fill_diagonal(sim, -1.0)
    members = np.asarray(sorted(rng.choice(60, size=41, replace=False)), dtype=np.intp)
    expected = [
        max(float(sim[i][j]) for j in members if j != i) for i in members
    ]
    with patch.object(ranking, "_ROW_BLOCK", block):
        got = ranking._strongest_internal(sim, members)
    assert got.tolist() == expected


# ── recall_memory keyword half ──────────────────────────────────────────


def _keyword_stores(tmp_path, facts):
    fact_store = MagicMock()
    fact_store.aload_facts = AsyncMock(return_value=facts)
    fact_store._facts_archive_path = MagicMock(return_value=str(tmp_path / "none.json"))
    reflection_engine = MagicMock()
    reflection_engine.aload_reflections = AsyncMock(return_value=[])
    return fact_store, reflection_engine


@pytest.mark.asyncio
async def test_hybrid_recall_bm25_runs_off_the_event_loop_with_same_ranking(tmp_path):
    facts = [
        {"id": f"k{i}", "text": f"博士喜欢猫 第{i}条 记录", "score": 1.0}
        for i in range(30)
    ] + [{"id": "other", "text": "今天下雨了", "score": 1.0}]
    fact_store, reflection_engine = _keyword_stores(tmp_path, facts)
    loop_thread = threading.get_ident()
    seen = {}
    real = hr._bm25_rank

    def spy(query, pool, **kwargs):
        seen["thread"] = threading.get_ident()
        ranked = real(query, pool, **kwargs)
        seen["ranked"] = [d["id"] for d, _ in ranked]
        return ranked

    hr._invalidate_pool_cache()
    with patch.object(hr, "_cosine_rank", AsyncMock(return_value=[])), \
         patch.object(hr, "HYBRID_RECALL_BM25_THRESHOLD", 0.0), \
         patch.object(hr, "_bm25_rank", spy):
        result = await hr.hybrid_recall(
            lanlan_name="parity", query="博士 猫", fact_store=fact_store,
            reflection_engine=reflection_engine, config_manager=MagicMock(),
        )
    assert seen["thread"] != loop_thread
    expected = [d["id"] for d, _ in real("博士 猫", hr._tag_tier(facts, "fact"), stop_names=[])]
    assert seen["ranked"] == expected
    assert "other" not in expected  # no shared term, so BM25 drops it
    assert [r["id"] for r in result["results"]][:hr.HYBRID_RECALL_BUDGET_EACH] == (
        expected[:hr.HYBRID_RECALL_BUDGET_EACH]
    )


@pytest.mark.asyncio
async def test_cancelling_recall_during_bm25_cancels_the_cosine_task(tmp_path):
    facts = [{"id": "k1", "text": "博士喜欢猫", "score": 1.0}]
    fact_store, reflection_engine = _keyword_stores(tmp_path, facts)
    entered = threading.Event()
    release = threading.Event()
    cosine_cancelled = asyncio.Event()

    def blocking_bm25(query, pool, **kwargs):
        entered.set()
        release.wait(10)
        return []

    async def slow_cosine(query, pool, **kwargs):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cosine_cancelled.set()
            raise
        return []

    hr._invalidate_pool_cache()
    with patch.object(hr, "_cosine_rank", slow_cosine), \
         patch.object(hr, "_bm25_rank", blocking_bm25):
        task = asyncio.create_task(hr.hybrid_recall(
            lanlan_name="parity", query="博士", fact_store=fact_store,
            reflection_engine=reflection_engine, config_manager=MagicMock(),
        ))
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 5
            while not entered.is_set():
                if task.done():
                    task.result()  # surfaces a failure before BM25 started
                    pytest.fail("recall finished before BM25 started")
                assert loop.time() < deadline, "BM25 never started"
                await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.wait_for(cosine_cancelled.wait(), 5)
        finally:
            release.set()
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ── recall pool preparation ─────────────────────────────────────────────


def _legacy_prepare_pools(active_facts, active_reflections, archive_facts, persona_left_out,
                          *, lanlan_name, subjects, include_legacy_private, time_window):
    """``hybrid_recall``'s inline pool preparation before it moved off-loop, verbatim."""
    from memory.recall import MemoryRecallReranker
    from memory.scopes import filter_entries_for_subjects

    archive_facts = hr._drop_archive_overlap(archive_facts or [], active_facts, lanlan_name)
    facts_tagged = hr._tag_tier(active_facts, 'fact')
    refl_tagged = hr._tag_tier(active_reflections, 'reflection')
    arch_tagged = hr._tag_tier(archive_facts, 'fact_archive')
    persona_tagged = hr._tag_tier(persona_left_out, 'persona')
    facts_tagged = filter_entries_for_subjects(facts_tagged, subjects, include_legacy_private=include_legacy_private)
    refl_tagged = filter_entries_for_subjects(refl_tagged, subjects, include_legacy_private=include_legacy_private)
    arch_tagged = filter_entries_for_subjects(arch_tagged, subjects, include_legacy_private=include_legacy_private)
    persona_tagged = filter_entries_for_subjects(persona_tagged, subjects, include_legacy_private=include_legacy_private)
    if time_window is not None:
        ws, we = time_window
        facts_tagged = [d for d in facts_tagged if hr._overlaps_window(d, ws, we)]
        refl_tagged = [d for d in refl_tagged if hr._overlaps_window(d, ws, we)]
        arch_tagged = [d for d in arch_tagged if hr._overlaps_window(d, ws, we)]
        persona_tagged = [d for d in persona_tagged if hr._overlaps_window(d, ws, we)]
    bm25_pool_raw = facts_tagged + refl_tagged + arch_tagged + persona_tagged
    embedding_pool_raw = facts_tagged + refl_tagged + persona_tagged
    return (
        MemoryRecallReranker._hard_filter(bm25_pool_raw),
        MemoryRecallReranker._hard_filter(embedding_pool_raw),
    )


def _pool_rows(seed):
    """Facts / reflections / archive / persona rows mixing every branch the
    preparation takes: scopes, archive overlap, time stamps, hard-filter drops."""
    from datetime import datetime, timedelta

    from memory.scopes import MemorySubject

    rng = np.random.default_rng(seed)
    group = MemorySubject.group_chat("qq", "7788").as_entry_fields()
    member = MemorySubject.participant("discord", "alice").as_entry_fields()
    base = datetime(2026, 11, 1)

    def row(prefix, i):
        r = {"id": f"{prefix}{i}", "text": f"{prefix} 记忆 {i}", "score": 1.0}
        pick = int(rng.integers(0, 9))
        if pick == 0:
            r.update(group)
        elif pick == 1:
            r.update(member)
        elif pick == 2:
            r["subject_kind"] = "group_chat"  # malformed partial scope
        elif pick == 3:
            r["score"] = -1.0
        elif pick == 4:
            r["suppressed"] = True
        elif pick == 5:
            r["protected"] = True
        elif pick == 6:
            r["text"] = "   "
        if rng.random() < 0.7:
            start = base + timedelta(days=int(rng.integers(0, 30)))
            r["event_start_at"] = start.isoformat()
            r["event_end_at"] = (start + timedelta(days=int(rng.integers(0, 3)))).isoformat()
        return r

    facts = [row("f", i) for i in range(40)]
    reflections = [dict(row("r", i), status=s) for i, s in zip(range(20), ["pending", "denied", "confirmed", "merged"] * 5)]
    archive = [row("a", i) for i in range(30)] + [dict(facts[i]) for i in range(0, 40, 7)]
    persona = [row("p", i) for i in range(15)]
    return facts, reflections, archive, persona, group, member


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("scenario", ["private", "group", "group+legacy", "window", "group+window"])
def test_prepare_recall_pools_matches_inline_preparation(seed, scenario):
    from datetime import datetime

    from memory.scopes import MemorySubject

    facts, reflections, archive, persona, _g, _m = _pool_rows(seed)
    subjects = None
    include_legacy = None
    window = None
    if "group" in scenario:
        subjects = [MemorySubject.group_chat("qq", "7788")]
    if "legacy" in scenario:
        include_legacy = True
    if "window" in scenario:
        window = (datetime(2026, 11, 8), datetime(2026, 11, 20))
    kwargs = dict(lanlan_name="parity", subjects=subjects,
                  include_legacy_private=include_legacy, time_window=window)
    expected = _legacy_prepare_pools(facts, reflections, archive, persona, **kwargs)
    got = hr._prepare_recall_pools(facts, reflections, archive, persona, **kwargs)
    assert got == expected
    assert expected[0], "fixture should leave a keyword pool"
    # Rows handed onward are private copies, never the stores' own dicts.
    store_ids = {id(r) for r in facts + reflections + archive + persona}
    assert not any(id(r) in store_ids for pool in got for r in pool)


@pytest.mark.asyncio
async def test_hybrid_recall_prepares_pools_off_the_event_loop(tmp_path):
    facts = [{"id": f"k{i}", "text": f"博士喜欢猫 第{i}条", "score": 1.0} for i in range(5)]
    fact_store, reflection_engine = _keyword_stores(tmp_path, facts)
    loop_thread = threading.get_ident()
    seen = []
    real = hr._prepare_recall_pools

    def spy(*args, **kwargs):
        seen.append(threading.get_ident())
        return real(*args, **kwargs)

    hr._invalidate_pool_cache()
    with patch.object(hr, "_cosine_rank", AsyncMock(return_value=[])), \
         patch.object(hr, "HYBRID_RECALL_BM25_THRESHOLD", 0.0), \
         patch.object(hr, "_prepare_recall_pools", spy):
        result = await hr.hybrid_recall(
            lanlan_name="parity", query="博士 猫", fact_store=fact_store,
            reflection_engine=reflection_engine, config_manager=MagicMock(),
        )
    assert seen and all(t != loop_thread for t in seen)
    assert result["results"]


@pytest.mark.asyncio
async def test_pool_prep_reads_snapshots_not_the_stores_live_lists(tmp_path):
    """Writers shrink FactStore's cached list in place from the event loop
    (``facts[:] = ...``). The worker must iterate a snapshot taken before it
    started, or such a write makes it skip rows nobody touched."""
    facts = [{"id": f"k{i}", "text": f"博士喜欢猫 第{i}条", "score": 1.0} for i in range(30)]
    original_ids = [f["id"] for f in facts]
    fact_store, reflection_engine = _keyword_stores(tmp_path, facts)
    real_drop = hr._drop_archive_overlap
    real_prepare = hr._prepare_recall_pools
    passed = {}

    def drop_then_shrink(archive_rows, active_rows, name):
        # The worker has started; a loop-side writer now reshapes the live list.
        facts[:] = facts[5:]
        return real_drop(archive_rows, active_rows, name)

    def spy_prepare(active_facts, *args, **kwargs):
        passed["is_live_list"] = active_facts is facts
        return real_prepare(active_facts, *args, **kwargs)

    hr._invalidate_pool_cache()
    with patch.object(hr, "_drop_archive_overlap", drop_then_shrink), \
         patch.object(hr, "_prepare_recall_pools", spy_prepare), \
         patch.object(hr, "_cosine_rank", AsyncMock(return_value=[])), \
         patch.object(hr, "HYBRID_RECALL_BM25_THRESHOLD", 0.0), \
         patch.object(hr, "_bm25_rank", lambda q, pool, **kw: [(d, 1.0) for d in pool]), \
         patch.object(hr, "HYBRID_RECALL_BUDGET_EACH", 100), \
         patch.object(hr, "HYBRID_RECALL_BUDGET_TOTAL", 100):
        result = await hr.hybrid_recall(
            lanlan_name="parity", query="博士 猫", fact_store=fact_store,
            reflection_engine=reflection_engine, config_manager=MagicMock(),
        )
    assert passed["is_live_list"] is False
    assert len(facts) == 25  # the write did happen
    assert sorted(r["id"] for r in result["results"]) == sorted(original_ids)
