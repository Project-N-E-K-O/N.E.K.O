"""Per-turn cost of passive turn context: what each text turn no longer redoes.

Passive retrieval runs on every text turn, so work that used to happen only
when the model called a tool now happens per message. These pin the caches
and shortcuts that keep it cheap, and that each one returns exactly what the
uncached path returned:

* BM25 serves pool tokenizations from a cache and hands back the term counts
  it already has (no second tokenizing pass);
* recall reads ``reflections.json`` once, and reflection token counts are
  remembered across renders;
* a name lookup stops growing spans past the longest name in the index;
* the vector cut ranks entries in numpy instead of a per-chunk Python loop;
* a newer memory request for a character makes the older one stop.
"""

from __future__ import annotations

import asyncio
import json
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import memory.embeddings as emb
import memory.hybrid_recall as hr
import memory.turn_context as mtc
from knowledge.models import parse_pack
from knowledge.retrieval import SemanticMatch, semantic_candidates
from knowledge.store import NAME_SPAN_MAX_CHARS, KnowledgeStore, _NAME_UNIT_RE
from knowledge.text import loose_surface, strict_surface
from tests.unit.test_knowledge_subsystem import _pack, fast_indexer  # noqa: F401
from tests.unit.test_memory_turn_context import _fact, _no_vectors, _stores

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_caches():
    hr._invalidate_pool_cache()
    mtc._probes.vectors.clear()
    mtc._backgrounds.clear()
    yield
    hr._invalidate_pool_cache()
    mtc._probes.vectors.clear()
    mtc._backgrounds.clear()


_POOL_TEXTS = [
    "用户最爱吃海底捞",
    "用户周末喜欢去东京旅行",
    "主人养了一只橘猫叫团子",
    "User likes rubber duck debugging",
    "用户今天加班到很晚，明天还要早起",
    "",
]


def _pool():
    return [{"id": f"d{i}", "text": text} for i, text in enumerate(_POOL_TEXTS)]


# ── BM25 token cache ────────────────────────────────────────────────

def test_bm25_scores_return_the_query_terms_each_doc_holds():
    query = "周末去东京吃海底捞"
    query_terms = set(hr._tokenize(query, []))
    scored = hr._bm25_scores(query, _pool(), stop_names=[])
    assert scored
    for doc, _score, tf in scored:
        assert set(tf) == set(hr._tokenize(doc["text"], [])) & query_terms
        assert all(count > 0 for count in tf.values())


def _reference_bm25(query, pool, stop_names, k1=1.5, b=0.75):
    """``hr._bm25_rank`` as it scored before the cache and the shortcuts."""
    import math

    query_terms = hr._tokenize(query, stop_names)
    if not query or not pool or not query_terms:
        return []
    doc_terms_list = [hr._tokenize(d.get("text", "") or "", stop_names) for d in pool]
    n_docs = len(pool)
    total_len = sum(len(t) for t in doc_terms_list)
    if total_len == 0:
        return []
    avgdl = total_len / n_docs
    query_unique = set(query_terms)
    df = dict.fromkeys(query_unique, 0)
    doc_tf_list = []
    for terms in doc_terms_list:
        tf_map = {}
        for t in terms:
            if t in query_unique:
                tf_map[t] = tf_map.get(t, 0) + 1
        doc_tf_list.append(tf_map)
        for t in tf_map:
            df[t] += 1
    scored = []
    for doc, doc_terms, doc_tf in zip(pool, doc_terms_list, doc_tf_list):
        if not doc_terms:
            continue
        norm = 1.0 - b + b * len(doc_terms) / avgdl
        score = 0.0
        for q_term in query_unique:
            n = df.get(q_term, 0)
            if n <= 0:
                continue
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


def test_bm25_scores_are_bit_identical_to_the_reference_scan():
    rng = random.Random(11)
    chars = "我你他的了是在吃海底捞周末东京旅行猫咪主人朋友喜欢 abc debugging duck"
    for _ in range(60):
        pool = [
            {"id": str(i), "text": "".join(rng.choice(chars) for _ in range(rng.randint(0, 30)))}
            for i in range(rng.randint(1, 40))
        ]
        query = "".join(rng.choice(chars) for _ in range(rng.randint(1, 15)))
        for _repeat in range(2):  # cold, then from the token cache
            got = hr._bm25_rank(query, pool, stop_names=["主人"])
            want = _reference_bm25(query, pool, ["主人"])
            assert [(doc["id"], score) for doc, score in got] == [
                (doc["id"], score) for doc, score in want
            ]


def test_cached_tokens_score_exactly_like_fresh_ones():
    query = "周末去东京吃海底捞 debugging"
    fresh = hr._bm25_rank(query, _pool(), stop_names=["主人"])
    assert hr._TOKEN_CACHE  # the first pass filled it
    cached = hr._bm25_rank(query, _pool(), stop_names=["主人"])
    assert [(doc["id"], score) for doc, score in cached] == [
        (doc["id"], score) for doc, score in fresh
    ]


def test_a_cached_pool_is_not_tokenized_again():
    pool = _pool()
    hr._bm25_rank("海底捞", pool, stop_names=[])
    calls = []
    real = hr._tokenize

    def counting(text, stop_names):
        calls.append(text)
        return real(text, stop_names)

    with patch.object(hr, "_tokenize", counting):
        hr._bm25_rank("东京旅行", pool, stop_names=[])
    assert calls == ["东京旅行"]  # the query only


def test_token_cache_keys_on_the_stop_names():
    pool = [{"id": "a", "text": "小天喜欢海底捞"}]
    with_name = hr._bm25_scores("小天 海底捞", pool, stop_names=["小天"])
    without = hr._bm25_scores("小天 海底捞", pool, stop_names=[])
    assert "小天" not in with_name[0][2]
    assert "小天" in without[0][2]


def test_text_holding_the_separator_is_tokenized_but_not_cached():
    pool = [{"id": "a", "text": "海底捞\x00火锅"}]
    first = hr._bm25_scores("海底捞", pool, stop_names=[])
    assert not hr._TOKEN_CACHE
    assert hr._bm25_scores("海底捞", pool, stop_names=[]) == first


def test_token_cache_stays_under_its_byte_cap(monkeypatch):
    monkeypatch.setattr(hr, "HYBRID_RECALL_TOKEN_CACHE_MAX_BYTES", 4096)
    pool = [{"id": str(i), "text": f"用户第{i}次去海底捞吃火锅"} for i in range(200)]
    first = hr._bm25_rank("海底捞", pool, stop_names=[])
    assert hr._TOKEN_CACHE_BYTES <= 4096
    assert 0 < len(hr._TOKEN_CACHE) < len(pool)
    # A scan never evicts what it cached itself: the pool larger than the
    # cap keeps the same resident head and still scores the same.
    resident = list(hr._TOKEN_CACHE)
    assert hr._bm25_rank("海底捞", pool, stop_names=[]) == first
    assert list(hr._TOKEN_CACHE) == resident


# ── reflections: one read, remembered token counts ──────────────────

def test_render_reflection_split_matches_the_engine_filters():
    from datetime import datetime

    from memory._reflection.selection import filter_active_confirmed
    from memory.evidence import evidence_score

    now = datetime.now().isoformat()
    rows = [
        {"id": "p", "text": "a", "status": "pending", "created_at": now},
        {"id": "c", "text": "b", "status": "confirmed", "created_at": now, "reinforcement": 3.0},
        {"id": "s", "text": "c", "status": "confirmed", "suppress": True, "reinforcement": 3.0},
        {"id": "d", "text": "d", "status": "denied"},
        "not a row",
    ]
    pending, confirmed = hr._render_reflection_split(rows)
    assert [row["id"] for row in pending] == ["p"]
    expected = filter_active_confirmed(rows[:4], now=datetime.now(), score=evidence_score)
    assert [row["id"] for row in confirmed] == [row["id"] for row in expected]


async def test_passive_recall_reads_reflections_once(tmp_path):
    fact_store, reflection_engine, persona_manager = _stores(
        tmp_path,
        facts=[_fact("f1", "用户最爱吃海底捞")],
        reflections=[{"id": "r1", "text": "用户最近在减肥", "status": "pending"}],
    )
    with patch.object(emb, "get_embedding_service", MagicMock(return_value=_no_vectors())), \
         patch("memory.stop_names.collect_stop_names", return_value=[]):
        await mtc.amemory_turn_context(
            lanlan_name="小天", text="周末又去吃海底捞了", fact_store=fact_store,
            reflection_engine=reflection_engine, persona_manager=persona_manager,
            config_manager=MagicMock(), budget_ms=1000, language="zh",
        )
    assert reflection_engine.aload_reflections.await_count == 1
    reflection_engine.aget_pending_reflections.assert_not_awaited()
    reflection_engine.aget_confirmed_reflections.assert_not_awaited()
    _name, args, _kwargs = persona_manager.arecall_residency.mock_calls[0]
    assert [row["id"] for row in args[1]] == ["r1"]  # pending, from the same read


async def test_reflection_token_counts_are_remembered_without_writeback():
    from memory.persona import rendering

    rendering._TOKEN_COUNT_MEMO.clear()
    calls = []

    async def counting(text, *args, **kwargs):
        calls.append(text)
        return len(text)

    entry = {"id": "r", "text": "用户最近在减肥"}
    with patch.object(rendering, "acount_tokens", counting):
        first = await rendering.RenderingMixin._aget_cached_token_count(dict(entry), writeback=False)
        second = await rendering.RenderingMixin._aget_cached_token_count(dict(entry), writeback=False)
    assert first == second == len(entry["text"])
    assert calls == [entry["text"]]
    # The entry itself stays clean, as before.
    fresh = dict(entry)
    with patch.object(rendering, "acount_tokens", counting):
        await rendering.RenderingMixin._aget_cached_token_count(fresh, writeback=False)
    assert "token_count" not in fresh


async def test_remembered_token_counts_follow_the_tokenizer():
    from memory.persona import rendering

    rendering._TOKEN_COUNT_MEMO.clear()
    calls = []

    async def counting(text, *args, **kwargs):
        calls.append(text)
        return 7

    entry = {"text": "用户最近在减肥"}
    with patch.object(rendering, "acount_tokens", counting):
        with patch.object(rendering, "tokenizer_identity", return_value="tiktoken:a"):
            await rendering.RenderingMixin._aget_cached_token_count(dict(entry), writeback=False)
        with patch.object(rendering, "tokenizer_identity", return_value="heuristic:1"):
            await rendering.RenderingMixin._aget_cached_token_count(dict(entry), writeback=False)
    assert len(calls) == 2


# ── name lookups stop at the longest name ───────────────────────────

def _reference_named_candidates(store, text, pack_ids):
    """``named_candidates`` before the length cut: every span up to 40 chars."""
    view = strict_surface(text)
    spans = [match.span() for match in _NAME_UNIT_RE.finditer(view)]
    keys: set[str] = set()
    for first, (start, _end) in enumerate(spans):
        for _start, end in spans[first:]:
            if end - start > NAME_SPAN_MAX_CHARS:
                break
            span = view[start:end]
            keys.add(f"s:{span}")
            if not span.isalnum():
                loose = loose_surface(span)
                if loose:
                    keys.add(f"l:{loose}")
    clause, args = store._pack_clause(pack_ids, False, ())
    with store._read() as conn:
        return sorted(
            int(row[0])
            for row in conn.execute(
                "SELECT DISTINCT s.entry_id FROM surfaces s JOIN entries e ON e.id=s.entry_id"
                f" WHERE s.surface IN (SELECT value FROM json_each(?)){clause}",
                (json.dumps(sorted(keys), ensure_ascii=False), *args),
            )
        )


_NAMES = [
    "海底捞", "水神", "New-York", "Re:Zero", "C++", "AT&T", "「绝绝子」", "rubber duck",
    "東京タワー", "서울 타워", "Café Noir", "x", "一二三四五六七八九十一二三四五六七八九十",
]


def _names_pack():
    entries = [
        {"title": f"条目{i}", "terms": {"alias": [name]}, "summary": "s", "content": "c"}
        for i, name in enumerate(_NAMES)
    ]
    return parse_pack(_pack("names", entries=entries))


def test_named_candidates_find_what_the_full_span_scan_found(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    store.replace_pack(_names_pack(), pack_sha256="0" * 64)
    rng = random.Random(7)
    filler = ["我", "今天", "去了", "，", " ", "and", "the", "!", "「", "」", "-", ":", "吃"]
    for _ in range(300):
        parts = [rng.choice(filler + _NAMES) for _ in range(rng.randint(1, 30))]
        joiner = rng.choice(["", " "])
        text = joiner.join(parts)
        got = sorted(store.named_candidates(text, pack_ids=["names"], limit=1000))
        assert got == _reference_named_candidates(store, text, ["names"]), text


def test_named_candidates_build_spans_no_longer_than_the_longest_name(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    store.replace_pack(parse_pack(_pack("short", entries=[
        {"title": "海底捞", "summary": "s", "content": "c"},
    ])), pack_sha256="0" * 64)
    seen: list[str] = []
    real = json.dumps

    def spy(value, *args, **kwargs):
        if isinstance(value, list):
            seen.extend(value)
        return real(value, *args, **kwargs)

    with patch("knowledge.store.json.dumps", spy):
        assert store.named_candidates("周末" * 100 + "海底捞", pack_ids=["short"])
    assert seen and max(len(key) - 2 for key in seen) <= len("海底捞")


def test_a_loose_name_is_found_in_a_span_longer_than_every_name(tmp_path):
    # "re - zero" is 9 characters, longer than any name here; only its six
    # letters count against the longest loose name ("rezero").
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    store.replace_pack(parse_pack(_pack("loose-names", entries=[
        {"title": "Re:Zero", "summary": "s", "content": "c"},
    ])), pack_sha256="0" * 64)
    assert store._name_lengths() == (len("re:zero"), len("rezero"))
    assert store.named_candidates("I love re - zero so much", pack_ids=["loose-names"])


def test_name_lengths_follow_pack_writes(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    store.replace_pack(parse_pack(_pack("short-names", entries=[
        {"title": "海底捞", "summary": "s", "content": "c"},
    ])), pack_sha256="0" * 64)
    long_name = "超级无敌海底捞火锅旗舰店"
    assert not store.named_candidates(f"去{long_name}吃饭", pack_ids=["long-names"])
    store.replace_pack(parse_pack(_pack("long-names", entries=[
        {"title": long_name, "summary": "s", "content": "c"},
    ])), pack_sha256="1" * 64)
    assert store.named_candidates(f"去{long_name}吃饭", pack_ids=["long-names"])
    store.delete_pack("long-names")
    assert store._name_lengths()[0] == len("海底捞")


# ── the vector cut, in numpy ────────────────────────────────────────

def _reference_semantic(snapshot, query_vector, allowed, excluded, limit, threshold):
    """``semantic_candidates`` as the per-chunk Python scan computed it."""
    wanted = set(allowed)
    packs = [index for index, pack_id in enumerate(snapshot.pack_ids) if pack_id in wanted]
    mask = np.isin(snapshot.chunk_pack_index, np.asarray(packs, dtype=np.int32))
    if not mask.any():
        return []
    scores = snapshot.matrix[mask] @ query_vector
    entry_ids = snapshot.entry_ids[mask].tolist()
    chunks = (
        snapshot.chunk_indexes[mask].tolist()
        if snapshot.chunk_indexes is not None else [None] * len(entry_ids)
    )
    best: dict[int, SemanticMatch] = {}
    for entry_id, score, chunk in zip(entry_ids, scores.tolist(), chunks):
        if entry_id in excluded:
            continue
        current = best.get(entry_id)
        if score >= threshold and (current is None or score > current.score):
            best[entry_id] = SemanticMatch(entry_id, score, chunk)
    return sorted(best.values(), key=lambda match: -match.score)[:limit]


def test_semantic_candidates_match_the_per_chunk_scan():
    rng = np.random.default_rng(3)
    for trial in range(200):
        count = int(rng.integers(1, 200))
        # Coarse values make many equal scores, so tie order is checked too.
        # Quarters, not unit rows: every score is then a multiple of 1/16 that
        # float32 holds exactly in any summation order. Normalised rows put
        # scores on the thresholds up to an ulp, and which side they land on
        # depends on the BLAS kernel the CPU picks and on the product's shape.
        matrix = rng.integers(-2, 3, (count, 8)).astype(np.float32) / 4
        snapshot = SimpleNamespace(
            matrix=matrix,
            pack_ids=["a", "b", "c"],
            chunk_pack_index=rng.integers(0, 3, count).astype(np.int32),
            entry_ids=rng.integers(0, max(count // 2, 1), count).astype(np.int64),
            chunk_indexes=(np.arange(count) % 5) if trial % 2 else None,
        )
        query = matrix[int(rng.integers(0, count))]
        allowed = list(rng.choice(["a", "b", "c"], size=int(rng.integers(1, 4)), replace=False))
        excluded = set(rng.integers(0, max(count // 2, 1), int(rng.integers(0, 5))).tolist())
        # -2.0 is the lowest score (keeps every chunk); 0.0 and 0.5 are scores
        # many chunks hit exactly, so ">=" is checked at the boundary.
        for threshold in (-2.0, 0.0, 0.3, 0.5):
            for limit in (1, 3, 24):
                got = semantic_candidates(
                    snapshot, query, allowed_pack_ids=allowed,
                    exclude_entry_ids=excluded, limit=limit, threshold=threshold,
                )
                want = _reference_semantic(snapshot, query, allowed, excluded, limit, threshold)
                assert [(m.entry_id, m.chunk_index) for m in got] == [
                    (m.entry_id, m.chunk_index) for m in want
                ]
                assert [m.score for m in got] == [m.score for m in want]
                assert all(type(m.entry_id) is int and type(m.score) is float for m in got)


# ── one memory request per character at a time ─────────────────────

async def test_a_newer_memory_request_supersedes_the_older_one(tmp_path):
    fact_store, reflection_engine, persona_manager = _stores(
        tmp_path, facts=[_fact("f1", "用户最爱吃海底捞"), _fact("f2", "用户今天加班到很晚")],
    )
    release = asyncio.Event()
    facts = list(fact_store.aload_facts.return_value)
    calls = 0

    async def slow_first(_name):
        nonlocal calls
        calls += 1
        if calls == 1:
            await release.wait()
        return facts

    fact_store.aload_facts = AsyncMock(side_effect=slow_first)
    kwargs = dict(
        lanlan_name="小天", fact_store=fact_store, reflection_engine=reflection_engine,
        persona_manager=persona_manager, config_manager=MagicMock(), language="zh",
    )
    with patch.object(emb, "get_embedding_service", MagicMock(return_value=_no_vectors())), \
         patch("memory.stop_names.collect_stop_names", return_value=[]):
        older = asyncio.create_task(mtc.amemory_turn_context(text="海底捞", budget_ms=5000, **kwargs))
        await asyncio.sleep(0.01)
        newer = asyncio.create_task(
            mtc.amemory_turn_context(text="周末又去吃海底捞了", budget_ms=5000, **kwargs)
        )
        await asyncio.sleep(0.01)
        assert not newer.done()  # waits for the older one to stop
        release.set()
        older_result, newer_result = await asyncio.gather(older, newer)
    assert older_result["result"] == "superseded"
    assert newer_result["result"] == "matched"
    assert newer_result["memory_ids"] == ["f1"]


async def test_a_request_still_running_past_the_budget_reads_as_busy(tmp_path):
    fact_store, reflection_engine, persona_manager = _stores(tmp_path, facts=[_fact("f1", "海底捞")])
    kwargs = dict(
        lanlan_name="小天", text="海底捞", fact_store=fact_store,
        reflection_engine=reflection_engine, persona_manager=persona_manager,
        config_manager=MagicMock(), language="zh",
    )
    _previous, running = mtc._claim("小天")  # an older request still in a worker
    with patch.object(emb, "get_embedding_service", MagicMock(return_value=_no_vectors())), \
         patch("memory.stop_names.collect_stop_names", return_value=[]):
        first = await mtc.amemory_turn_context(budget_ms=200, **kwargs)
        # The busy request did no work, but the older one still runs: the
        # next request must wait for it as well, not start alongside it.
        second = await mtc.amemory_turn_context(budget_ms=200, **kwargs)
        assert first["result"] == second["result"] == "busy"
        fact_store.aload_facts.assert_not_awaited()

        mtc._release("小天", running)
        for _ in range(50):  # each link of the chain releases on its own loop pass
            if not mtc._in_flight.get(asyncio.get_running_loop()):
                break
            await asyncio.sleep(0)
        assert not mtc._in_flight.get(asyncio.get_running_loop())
        third = await mtc.amemory_turn_context(budget_ms=1000, **kwargs)
    assert third["result"] != "busy"
    fact_store.aload_facts.assert_awaited()
    assert not mtc._in_flight.get(asyncio.get_running_loop())


async def test_a_cancelled_request_keeps_its_slot_until_its_thread_ends(tmp_path):
    import contextlib
    import threading

    fact_store, reflection_engine, persona_manager = _stores(tmp_path, facts=[_fact("f1", "海底捞")])
    kwargs = dict(
        lanlan_name="小天", text="海底捞", fact_store=fact_store,
        reflection_engine=reflection_engine, persona_manager=persona_manager,
        config_manager=MagicMock(), language="zh",
    )
    started, proceed = threading.Event(), threading.Event()
    real = hr._bm25_scores

    def slow_scores(*args, **kw):
        started.set()
        proceed.wait(5)
        return real(*args, **kw)

    loop = asyncio.get_running_loop()
    with patch.object(hr, "_bm25_scores", slow_scores), \
         patch.object(emb, "get_embedding_service", MagicMock(return_value=_no_vectors())), \
         patch("memory.stop_names.collect_stop_names", return_value=[]):
        first = asyncio.create_task(mtc.amemory_turn_context(budget_ms=5000, **kwargs))
        while not started.is_set():
            await asyncio.sleep(0.005)
        first.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first
        # The cancelled request's BM25 thread still runs: no second pass yet.
        second = await mtc.amemory_turn_context(budget_ms=200, **kwargs)
        assert second["result"] == "busy"

        proceed.set()
        for _ in range(500):
            if not mtc._in_flight.get(loop):
                break
            await asyncio.sleep(0.01)
        assert not mtc._in_flight.get(loop)
        third = await mtc.amemory_turn_context(budget_ms=1000, **kwargs)
    assert third["result"] != "busy"


async def test_a_cancelled_embedding_keeps_the_slot_until_it_drains(tmp_path):
    # The embedding service keeps a cancelled inference task alive until its
    # native thread exits; the slot must wait for that too.
    fact_store, reflection_engine, persona_manager = _stores(tmp_path, facts=[_fact("f1", "海底捞")])
    kwargs = dict(
        lanlan_name="小天", text="海底捞", fact_store=fact_store,
        reflection_engine=reflection_engine, persona_manager=persona_manager,
        config_manager=MagicMock(), language="zh",
    )
    drained = asyncio.Event()

    async def embed_batch(texts):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await drained.wait()  # like _run_blocking waiting out its worker
            raise
        return [np.ones(8, dtype=np.float32) for _ in texts]

    service = MagicMock()
    service.is_available = MagicMock(return_value=True)
    service.model_id = MagicMock(return_value="local-text-retrieval-v1-8d-int8")
    service.embed_batch = embed_batch
    loop = asyncio.get_running_loop()
    with patch.object(emb, "get_embedding_service", MagicMock(return_value=service)), \
         patch.object(mtc, "_ensure_probe_vectors", MagicMock()), \
         patch("memory.stop_names.collect_stop_names", return_value=[]):
        try:
            first = await mtc.amemory_turn_context(budget_ms=200, **kwargs)
            assert first["result"] in {"miss", "matched"}  # answered within budget
            assert mtc._in_flight.get(loop)  # but the inference still runs
            second = await mtc.amemory_turn_context(budget_ms=200, **kwargs)
            assert second["result"] == "busy"
        finally:
            drained.set()
        for _ in range(100):
            if not mtc._in_flight.get(loop):
                break
            await asyncio.sleep(0.01)
        assert not mtc._in_flight.get(loop)


async def test_a_timed_out_knowledge_query_skips_the_other_material_type(tmp_path, fast_indexer):  # noqa: F811
    """Each material type's vector scan is its own worker hop, so a query that
    times out during the first one never starts the second."""
    import threading

    from tests.unit.test_knowledge_turn_context import TopicEmbedder, _entry, _ready

    knowledge = _pack("lore", entries=[_entry("角色", "这是一位水神。")])
    corpus = _pack("lines", material_type="corpus", entries=[_entry("台词", "愿神明保佑你。")])
    service = await _ready(tmp_path, TopicEmbedder(), packs=[knowledge, corpus])
    try:
        import knowledge.service as service_module

        scanned: list[tuple[str, ...]] = []
        release = threading.Event()
        real = service_module.semantic_candidates

        def slow_scan(*args, **kwargs):
            scanned.append(tuple(kwargs.get("allowed_pack_ids", ())))
            release.wait(5)
            return real(*args, **kwargs)

        patcher = patch.object(service_module, "semantic_candidates", slow_scan)
        patcher.start()
        result = await service.turn_context(text="我想多了解一点神明", budget_ms=300)
        assert result["result"] == "timeout"
        release.set()
        await asyncio.sleep(0.3)
        assert len(scanned) == 1
    finally:
        release.set()
        patch.stopall()
        await service.stop()


async def test_a_request_superseded_during_cosine_scoring_stops_there(tmp_path):
    import threading

    from tests.unit.test_memory_turn_context import _vectors

    vector = np.ones(8, dtype=np.float32)
    fact_store, reflection_engine, persona_manager = _stores(
        tmp_path, facts=[_fact("f1", "用户最爱吃海底捞", vector=vector)],
    )
    kwargs = dict(
        lanlan_name="小天", fact_store=fact_store, reflection_engine=reflection_engine,
        persona_manager=persona_manager, config_manager=MagicMock(), language="zh",
    )
    started, proceed = threading.Event(), threading.Event()
    real = mtc._score
    calls = 0

    def slow_score(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            proceed.wait(5)
        return real(*args)

    rendered = []
    real_render = mtc._render

    def spy_render(picked, language):
        rendered.append([doc.get("id") for doc in picked])
        return real_render(picked, language)

    with patch.object(mtc, "_score", slow_score), patch.object(mtc, "_render", spy_render), \
         patch.object(emb, "get_embedding_service", MagicMock(return_value=_vectors(vector))), \
         patch.object(mtc, "_ensure_probe_vectors", MagicMock()), \
         patch("memory.stop_names.collect_stop_names", return_value=[]):
        older = asyncio.create_task(mtc.amemory_turn_context(text="海底捞", budget_ms=5000, **kwargs))
        try:
            while not started.is_set():
                await asyncio.sleep(0.005)
            newer = asyncio.create_task(mtc.amemory_turn_context(text="周末吃海底捞", budget_ms=5000, **kwargs))
            await asyncio.sleep(0.01)
        finally:
            proceed.set()
        older_result, _newer_result = await asyncio.gather(older, newer)
    assert older_result["result"] == "superseded"
    assert calls == 2  # the newer one scored; the older stopped right after
    assert len(rendered) <= 1  # only the newer request may render


async def test_a_cancelled_pool_load_keeps_the_slot_until_it_ends(tmp_path):
    import contextlib
    import threading

    fact_store, reflection_engine, persona_manager = _stores(tmp_path, facts=[_fact("f1", "海底捞")])
    facts = list(fact_store.aload_facts.return_value)
    started, proceed = threading.Event(), threading.Event()

    def cold_parse():
        started.set()
        proceed.wait(5)
        return facts

    async def load_facts(_name):
        return await asyncio.to_thread(cold_parse)

    fact_store.aload_facts = AsyncMock(side_effect=load_facts)
    kwargs = dict(
        lanlan_name="小天", text="海底捞", fact_store=fact_store,
        reflection_engine=reflection_engine, persona_manager=persona_manager,
        config_manager=MagicMock(), language="zh",
    )
    loop = asyncio.get_running_loop()
    with patch.object(emb, "get_embedding_service", MagicMock(return_value=_no_vectors())), \
         patch("memory.stop_names.collect_stop_names", return_value=[]):
        first = asyncio.create_task(mtc.amemory_turn_context(budget_ms=5000, **kwargs))
        try:
            while not started.is_set():
                await asyncio.sleep(0.005)
            first.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await first
            second = await mtc.amemory_turn_context(budget_ms=200, **kwargs)
            assert second["result"] == "busy"
            assert fact_store.aload_facts.await_count == 1  # no second parse
        finally:
            proceed.set()
        for _ in range(500):
            if not mtc._in_flight.get(loop):
                break
            await asyncio.sleep(0.01)
        assert not mtc._in_flight.get(loop)


async def test_a_request_left_without_budget_after_waiting_does_not_start(tmp_path):
    fact_store, reflection_engine, persona_manager = _stores(tmp_path, facts=[_fact("f1", "海底捞")])
    _previous, running = mtc._claim("小天")
    loop = asyncio.get_running_loop()
    # The predecessor ends just before this request's wait would time out:
    # 300 ms budget, 100 ms render reserve, done at 180 ms -> 20 ms left.
    loop.call_later(0.18, mtc._release, "小天", running)
    with patch.object(emb, "get_embedding_service", MagicMock(return_value=_no_vectors())):
        result = await mtc.amemory_turn_context(
            lanlan_name="小天", text="海底捞", fact_store=fact_store,
            reflection_engine=reflection_engine, persona_manager=persona_manager,
            config_manager=MagicMock(), budget_ms=300, language="zh",
        )
    assert result["result"] == "busy"
    fact_store.aload_facts.assert_not_awaited()
