# -*- coding: utf-8 -*-
"""Unit tests for memory.recall.MemoryRecallReranker.

The reranker has three phases (hard filter → coarse cosine rank →
LLM fine rank). Tests cover each phase in isolation plus the
end-to-end ``aretrieve_candidates`` flow including the fallback path
when the EmbeddingService is disabled.

We never call a real LLM — the fine-rank tests stub
`utils.llm_client.create_chat_llm` the same way PR #941's
test_persona_version_history.py does."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from memory.embeddings import (
    _embedding_text_sha256,
    reset_embedding_service_for_tests,
)
from memory.recall import COARSE_OVERSAMPLE, MemoryRecallReranker


# ── stub embedding service ───────────────────────────────────────────


class _FakeService:
    """Minimal stand-in for EmbeddingService used by recall tests.
    The reranker only touches a handful of methods; keep the stub
    narrow so tests stay readable."""

    def __init__(
        self, *, available: bool = True, model_id: str = "fake-128d-int8",
        vector_factory=None,
    ) -> None:
        self._available = available
        self._model_id = model_id
        self._vector_factory = vector_factory or (lambda text: [1.0, 0.0])
        self.embed_calls: list[list[str]] = []

    def is_available(self) -> bool:
        return self._available

    def model_id(self):
        return self._model_id if self._available else None

    async def embed_batch(self, texts):
        self.embed_calls.append(list(texts))
        return [self._vector_factory(t) if t else None for t in texts]


@pytest.fixture(autouse=True)
def _isolate_singleton():
    reset_embedding_service_for_tests()
    yield
    reset_embedding_service_for_tests()


def _make_reranker(service: _FakeService) -> MemoryRecallReranker:
    r = MemoryRecallReranker()
    r._service = service
    return r


def _obs(oid: str, text: str, *, score: float = 1.0,
         entity: str = "master",
         target_type: str = "persona",
         embedding: list[float] | None = None,
         model_id: str = "fake-128d-int8",
         status: str | None = None,
         suppress: bool = False,
         protected: bool = False) -> dict:
    """Build an observation dict in the shape ``_aload_signal_targets``
    emits — keep all the keys the reranker may inspect."""
    o = {
        "id": oid,
        "raw_id": oid.split(".")[-1],
        "target_type": target_type,
        "entity": entity,
        "entity_key": entity,
        "text": text,
        "score": score,
        "suppress": suppress,
        "protected": protected,
        "embedding": list(embedding) if embedding is not None else None,
        "embedding_text_sha256": _embedding_text_sha256(text) if embedding else None,
        "embedding_model_id": model_id if embedding else None,
    }
    if status is not None:
        o["status"] = status
    return o


def _make_llm_mock(payload):
    resp = MagicMock()
    resp.content = json.dumps(payload)

    async def _ainvoke(*_a, **_k):
        return resp

    async def _aclose():
        return None

    llm = MagicMock()
    llm.ainvoke = _ainvoke
    llm.aclose = _aclose
    return llm


# ── phase 1: hard filter ─────────────────────────────────────────────


def test_hard_filter_drops_negative_score():
    """User-disputed entries (evidence_score < 0) must not feed back
    into the LLM signal-detection pool — Stage-2 would either
    reinforce the dispute or, worse, cancel it."""
    obs = [
        _obs("p.master.a", "kept", score=1.5),
        _obs("p.master.b", "dropped", score=-0.5),
        _obs("p.master.c", "kept", score=0.0),
    ]
    out = MemoryRecallReranker._hard_filter(obs)
    ids = {o["id"] for o in out}
    assert "p.master.a" in ids
    assert "p.master.c" in ids
    assert "p.master.b" not in ids


def test_hard_filter_drops_suppressed():
    obs = [
        _obs("p.master.a", "kept"),
        _obs("p.master.b", "dropped", suppress=True),
    ]
    out = MemoryRecallReranker._hard_filter(obs)
    assert {o["id"] for o in out} == {"p.master.a"}


def test_hard_filter_drops_protected_persona():
    """character_card-sourced entries have effectively infinite
    evidence — they're never the target of a Stage-2 signal."""
    obs = [
        _obs("p.master.a", "kept"),
        _obs("p.master.card", "dropped", protected=True),
    ]
    out = MemoryRecallReranker._hard_filter(obs)
    assert {o["id"] for o in out} == {"p.master.a"}


def test_hard_filter_drops_only_dead_reflection_statuses():
    """The drop set is intentionally a *subset* of
    `REFLECTION_TERMINAL_STATUSES` — `promoted` is kept because the
    upstream `_aload_signal_targets` ships it as part of the
    `confirmed + promoted` Stage-2 observation pool (a promoted
    reflection is the strongest signal we have: confirmed,
    consolidated, still active).  Filtering it out here would
    silently shrink the rerank pool below what the legacy path
    produced (CodeRabbit PR-957 Major regression test).

    Truly-dead statuses (denied / archived / merged / promote_blocked)
    still get dropped — those are dead-letter or already consumed."""
    obs = [
        _obs("r.confirmed", "kept", target_type="reflection", status="confirmed"),
        _obs("r.promoted", "kept", target_type="reflection", status="promoted"),
    ]
    drop_statuses = ("denied", "archived", "merged", "promote_blocked")
    for status in drop_statuses:
        obs.append(_obs(f"r.{status}", "drop", target_type="reflection", status=status))
    out = MemoryRecallReranker._hard_filter(obs)
    ids = {o["id"] for o in out}
    assert "r.confirmed" in ids
    assert "r.promoted" in ids, (
        "promoted must NOT be filtered — upstream pool includes it"
    )
    for status in drop_statuses:
        assert f"r.{status}" not in ids


def test_hard_filter_drops_empty_text():
    """Defensive — an empty-text observation can't be embedded or
    matched; surface as zero-row noise rather than going through."""
    obs = [
        _obs("p.master.a", ""),
        _obs("p.master.b", "   "),
        _obs("p.master.c", "real text"),
    ]
    out = MemoryRecallReranker._hard_filter(obs)
    assert {o["id"] for o in out} == {"p.master.c"}


# ── phase 2: coarse rank by cosine ───────────────────────────────────


@pytest.mark.asyncio
async def test_coarse_rank_falls_back_when_service_unavailable():
    """No embedding service ⇒ pure evidence_score order, top-k slice."""
    svc = _FakeService(available=False)
    r = _make_reranker(svc)
    obs = [
        _obs("a", "x", score=0.5),
        _obs("b", "y", score=2.0),
        _obs("c", "z", score=1.0),
    ]
    out = await r._coarse_rank(obs, query_texts=["q"], k=3)
    assert [o["id"] for o in out] == ["b", "c", "a"]
    # Service was never asked to embed anything.
    assert svc.embed_calls == []


@pytest.mark.asyncio
async def test_coarse_rank_falls_back_when_no_query():
    """Empty/None query ⇒ no semantic basis, evidence order applies."""
    svc = _FakeService(available=True)
    r = _make_reranker(svc)
    obs = [
        _obs("a", "x", score=0.5),
        _obs("b", "y", score=2.0),
    ]
    out = await r._coarse_rank(obs, query_texts=None, k=2)
    assert [o["id"] for o in out] == ["b", "a"]


@pytest.mark.asyncio
async def test_coarse_rank_uses_max_cosine_across_queries():
    """A candidate that's near ANY query vector should win — recall
    should not require relevance to ALL topics. Implements the
    documented max-pool semantics."""
    # Two queries: q1 → vec [1,0], q2 → vec [0,1]. Candidates:
    #   a: [1,0]   → cosine vs q1 = 1.0, vs q2 = 0.0  ⇒ max = 1.0
    #   b: [0,1]   → cosine vs q1 = 0.0, vs q2 = 1.0  ⇒ max = 1.0
    #   c: [.5,.5] → cosine vs q1 = .707, vs q2 = .707 ⇒ max = .707
    qmap = {"q1": [1.0, 0.0], "q2": [0.0, 1.0]}
    svc = _FakeService(
        available=True,
        vector_factory=lambda text: qmap.get(text, [0.0, 0.0]),
    )
    r = _make_reranker(svc)
    obs = [
        _obs("a", "ta", score=0.0, embedding=[1.0, 0.0]),
        _obs("b", "tb", score=0.0, embedding=[0.0, 1.0]),
        _obs("c", "tc", score=0.0, embedding=[0.7071, 0.7071]),
    ]
    out = await r._coarse_rank(obs, query_texts=["q1", "q2"], k=3)
    # a and b tie at cosine 1.0 (both should rank above c).
    assert {out[0]["id"], out[1]["id"]} == {"a", "b"}
    assert out[2]["id"] == "c"


@pytest.mark.asyncio
async def test_coarse_rank_breaks_ties_with_evidence_score():
    """Two candidates with identical cosine should fall back to
    evidence_score for the tie-break — explicit assertion of the
    documented contract."""
    svc = _FakeService(
        available=True,
        vector_factory=lambda text: [1.0, 0.0],
    )
    r = _make_reranker(svc)
    obs = [
        _obs("a", "ta", score=0.5, embedding=[1.0, 0.0]),
        _obs("b", "tb", score=2.0, embedding=[1.0, 0.0]),
    ]
    out = await r._coarse_rank(obs, query_texts=["q"], k=2)
    assert [o["id"] for o in out] == ["b", "a"]


@pytest.mark.asyncio
async def test_coarse_rank_demotes_candidates_without_embedding():
    """A row with embedding=None falls below the cosine-ranked
    embedded ones — the LLM rerank can still pick it up if it's the
    right answer text-wise. With k=2 and one of each, both survive."""
    svc = _FakeService(
        available=True,
        vector_factory=lambda text: [1.0, 0.0],
    )
    r = _make_reranker(svc)
    obs = [
        _obs("with_vec", "t", score=0.0, embedding=[1.0, 0.0]),
        _obs("no_vec", "t", score=10.0),  # no embedding → no model_id
    ]
    out = await r._coarse_rank(obs, query_texts=["q"], k=2)
    assert [o["id"] for o in out] == ["with_vec", "no_vec"]


@pytest.mark.asyncio
async def test_coarse_rank_reserves_quota_for_unembedded_candidates():
    """CodeRabbit PR-957 Major regression: when there are ≥k embedded
    candidates, the previous implementation tagged un-embedded with
    cosine=-1 and let `[:k]` truncate them off entirely, so the LLM
    rerank never saw them despite the docstring promising they'd
    "fall through". Fix: split into two pools and reserve a quota
    for un-embedded entries (top by evidence_score) so they reach
    the LLM for text-based matching."""
    svc = _FakeService(
        available=True,
        vector_factory=lambda text: [1.0, 0.0],
    )
    r = _make_reranker(svc)
    # 6 embedded + 3 un-embedded; k=6 (would be budget*COARSE_OVERSAMPLE
    # in real call). Without the fix, all 6 slots go to embedded, and
    # the un-embedded ones (even with high evidence_score) get starved.
    obs = []
    for i in range(6):
        obs.append(_obs(f"emb{i}", "t", score=float(i), embedding=[1.0, 0.0]))
    for i in range(3):
        # High evidence_score so they'd win on a fairer ranking.
        obs.append(_obs(f"unemb{i}", "t", score=100.0 + i))
    out = await r._coarse_rank(obs, query_texts=["q"], k=6)
    # With the quota fix, at least one un-embedded entry must reach
    # the output so the LLM rerank gets a chance at it.
    out_ids = {o["id"] for o in out}
    unembedded_in_out = {oid for oid in out_ids if oid.startswith("unemb")}
    assert len(unembedded_in_out) >= 1, (
        "un-embedded entries must reserve at least one slot in the "
        "coarse pool — otherwise text-matching by LLM is impossible"
    )
    # And we still emit exactly k entries.
    assert len(out) == 6


@pytest.mark.asyncio
async def test_coarse_rank_unembedded_quota_picks_highest_evidence():
    """When the un-embedded slot count is smaller than the un-embedded
    pool, slot allocation goes to the highest-evidence_score entries —
    evidence is the only signal we have for them.

    k=4 with COARSE_OVERSAMPLE=3 yields quota = max(1, 4 // 4) = 1, so
    exactly one un-embedded entry should reach the output, and it
    should be the higher-score one."""
    svc = _FakeService(
        available=True,
        vector_factory=lambda text: [1.0, 0.0],
    )
    r = _make_reranker(svc)
    obs = [_obs(f"emb{i}", "t", score=0.0, embedding=[1.0, 0.0])
           for i in range(8)]
    obs.append(_obs("unemb_low", "t", score=1.0))
    obs.append(_obs("unemb_high", "t", score=99.0))
    out = await r._coarse_rank(obs, query_texts=["q"], k=4)
    out_ids = {o["id"] for o in out}
    # Quota=1 → only the higher-evidence un-embedded entry survives.
    assert "unemb_high" in out_ids
    assert "unemb_low" not in out_ids


@pytest.mark.asyncio
async def test_coarse_rank_no_unembedded_uses_full_quota_for_embedded():
    """When the pool has no un-embedded entries, the full k goes to
    embedded — quota mechanism mustn't waste slots on a pool that
    doesn't exist."""
    svc = _FakeService(
        available=True,
        vector_factory=lambda text: [1.0, 0.0],
    )
    r = _make_reranker(svc)
    obs = [_obs(f"emb{i}", "t", score=float(i), embedding=[1.0, 0.0])
           for i in range(6)]
    out = await r._coarse_rank(obs, query_texts=["q"], k=4)
    assert len(out) == 4
    assert all(o["id"].startswith("emb") for o in out)


# ── phase 3: LLM rerank ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aretrieve_skips_llm_when_candidates_within_budget():
    """When coarse rank already produces ≤ budget candidates, the LLM
    call is wasted (every candidate makes the cut). Skip it."""
    svc = _FakeService(available=True, vector_factory=lambda t: [1.0, 0.0])
    r = _make_reranker(svc)
    cm = MagicMock()
    obs = [
        _obs("a", "ta", embedding=[1.0, 0.0]),
        _obs("b", "tb", embedding=[1.0, 0.0]),
    ]
    create_llm = MagicMock()
    with patch("utils.llm_client.create_chat_llm", create_llm):
        out = await r.aretrieve_candidates(
            obs, query_texts=["q"], budget=5, config_manager=cm,
        )
    assert {o["id"] for o in out} == {"a", "b"}
    assert create_llm.call_count == 0


@pytest.mark.asyncio
async def test_aretrieve_invokes_llm_when_pool_exceeds_budget():
    """Coarse pool is 3 × budget by default; with budget=2 and 6+
    cosine-ranked candidates, the LLM should be asked to pick 2."""
    svc = _FakeService(available=True, vector_factory=lambda t: [1.0, 0.0])
    r = _make_reranker(svc)
    cm = MagicMock()
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    obs = [
        _obs(f"o{i}", f"t{i}", embedding=[1.0, 0.0]) for i in range(8)
    ]
    # LLM picks o3 then o5 — assert order is preserved in the output.
    fake_llm = _make_llm_mock([{"id": "o3"}, {"id": "o5"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        out = await r.aretrieve_candidates(
            obs, query_texts=["q"], budget=2, config_manager=cm,
        )
    assert [o["id"] for o in out] == ["o3", "o5"]


@pytest.mark.asyncio
async def test_aretrieve_normalises_blank_query_texts_before_llm_rerank():
    """``query_texts=[""]`` or ``["   "]`` slipped past the entry guard
    (which only checks falsiness of the list itself), reached coarse-
    rank as evidence_score fallback, but then triggered an LLM rerank
    with an empty {QUERY} placeholder — wasted tokens and unstable
    output. Strip + collapse-to-None so phase 2 and phase 3 see the
    same shape (CodeRabbit PR-956 Minor)."""
    svc = _FakeService(available=True, vector_factory=lambda t: [1.0, 0.0])
    r = _make_reranker(svc)
    cm = MagicMock()
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    obs = [
        _obs(f"o{i}", f"t{i}", embedding=[1.0, 0.0], score=10 - i) for i in range(8)
    ]
    fake_llm = _make_llm_mock([{"id": "o0"}, {"id": "o1"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm) as p:
        out = await r.aretrieve_candidates(
            obs, query_texts=["   ", ""], budget=2, config_manager=cm,
        )
    # No LLM call — blank queries collapsed to None ⇒ coarse fallback
    # to evidence_score order returns the [:budget] slice directly.
    assert p.call_count == 0
    assert [o["id"] for o in out] == ["o0", "o1"]


@pytest.mark.asyncio
async def test_aretrieve_drops_hallucinated_ids_from_llm():
    """LLM returns an id that isn't in the candidate set — must be
    silently dropped (not crash, not pass through)."""
    svc = _FakeService(available=True, vector_factory=lambda t: [1.0, 0.0])
    r = _make_reranker(svc)
    cm = MagicMock()
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    obs = [
        _obs(f"o{i}", f"t{i}", embedding=[1.0, 0.0]) for i in range(8)
    ]
    fake_llm = _make_llm_mock([
        {"id": "ghost"}, {"id": "o2"}, {"id": "another_ghost"}, {"id": "o5"},
    ])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        out = await r.aretrieve_candidates(
            obs, query_texts=["q"], budget=2, config_manager=cm,
        )
    assert [o["id"] for o in out] == ["o2", "o5"]


@pytest.mark.asyncio
async def test_aretrieve_tops_up_when_llm_returns_fewer_than_budget():
    """LLM only returns 1 item but budget=3 — top up from the coarse-
    rank tail so the caller still gets `budget` rows when there were
    that many candidates."""
    svc = _FakeService(available=True, vector_factory=lambda t: [1.0, 0.0])
    r = _make_reranker(svc)
    cm = MagicMock()
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    obs = [
        _obs(f"o{i}", f"t{i}", embedding=[1.0, 0.0], score=10 - i)
        for i in range(8)
    ]
    fake_llm = _make_llm_mock([{"id": "o5"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        out = await r.aretrieve_candidates(
            obs, query_texts=["q"], budget=3, config_manager=cm,
        )
    assert len(out) == 3
    assert out[0]["id"] == "o5"
    # The remaining two slots top up from the coarse-rank tail in
    # order — those are the highest-evidence_score rows the LLM
    # didn't pick. Position 0 already pinned to o5 above; here we
    # just assert the top-up didn't somehow drop o5.
    assert {o["id"] for o in out[1:]}.isdisjoint({"o5"})


@pytest.mark.asyncio
async def test_aretrieve_falls_back_to_coarse_on_llm_error():
    """LLM raises ⇒ best-effort fallback to coarse rank order; never
    crash the recall path. Stage-2 signal detection then runs against
    the coarse-ranked top-budget."""
    svc = _FakeService(available=True, vector_factory=lambda t: [1.0, 0.0])
    r = _make_reranker(svc)
    cm = MagicMock()
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    obs = [
        _obs(f"o{i}", f"t{i}", embedding=[1.0, 0.0], score=10 - i)
        for i in range(8)
    ]

    class _BoomLLM:
        async def ainvoke(self, *_a, **_k):
            raise RuntimeError("simulated network failure")

        async def aclose(self):
            return None

    with patch("utils.llm_client.create_chat_llm", return_value=_BoomLLM()):
        out = await r.aretrieve_candidates(
            obs, query_texts=["q"], budget=2, config_manager=cm,
        )
    # Falls back to coarse rank: cosine ties → score DESC ⇒ o0, o1.
    assert len(out) == 2
    assert {o["id"] for o in out} == {"o0", "o1"}


@pytest.mark.asyncio
async def test_aretrieve_skips_rerank_when_disabled():
    """rerank=False ⇒ coarse-rank top-budget directly. Used by tests
    and callers that want the cosine prefilter without the LLM cost."""
    svc = _FakeService(available=True, vector_factory=lambda t: [1.0, 0.0])
    r = _make_reranker(svc)
    cm = MagicMock()
    obs = [
        _obs(f"o{i}", f"t{i}", embedding=[1.0, 0.0], score=10 - i)
        for i in range(8)
    ]
    create_llm = MagicMock()
    with patch("utils.llm_client.create_chat_llm", create_llm):
        out = await r.aretrieve_candidates(
            obs, query_texts=["q"], budget=2,
            config_manager=cm, rerank=False,
        )
    assert create_llm.call_count == 0
    assert len(out) == 2


# ── full-pipeline fallback ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_aretrieve_full_fallback_when_service_disabled():
    """Vectors completely off ⇒ pipeline collapses to evidence_score
    order, top-budget. No LLM call. This is the path that runs when
    onnxruntime / model file isn't installed."""
    svc = _FakeService(available=False)
    r = _make_reranker(svc)
    cm = MagicMock()
    obs = [
        _obs(f"o{i}", f"t{i}", score=float(i))
        for i in range(10)
    ]
    create_llm = MagicMock()
    with patch("utils.llm_client.create_chat_llm", create_llm):
        out = await r.aretrieve_candidates(
            obs, query_texts=["q"], budget=3, config_manager=cm,
        )
    assert create_llm.call_count == 0
    assert [o["id"] for o in out] == ["o9", "o8", "o7"]


@pytest.mark.asyncio
async def test_aretrieve_empty_observations_returns_empty():
    svc = _FakeService(available=True)
    r = _make_reranker(svc)
    cm = MagicMock()
    out = await r.aretrieve_candidates(
        [], query_texts=["q"], budget=5, config_manager=cm,
    )
    assert out == []


@pytest.mark.asyncio
async def test_aretrieve_hard_filter_zero_returns_empty():
    """Every observation excluded by hard filter ⇒ empty result; no
    coarse rank, no LLM call."""
    svc = _FakeService(available=True)
    r = _make_reranker(svc)
    cm = MagicMock()
    obs = [
        _obs("a", "x", suppress=True),
        _obs("b", "y", protected=True),
        _obs("c", "z", score=-1.0),
    ]
    out = await r.aretrieve_candidates(
        obs, query_texts=["q"], budget=5, config_manager=cm,
    )
    assert out == []


# ── prompt locale coverage ───────────────────────────────────────────


def test_memory_recall_rerank_prompt_has_all_five_locales_with_placeholders():
    """All five locales rendered + every placeholder substituted —
    same contract test as fact_dedup.test_fact_dedup_prompt..."""
    from config.prompts_memory import (
        MEMORY_RECALL_RERANK_PROMPT,
        get_memory_recall_rerank_prompt,
    )
    expected_locales = {"zh", "en", "ja", "ko", "ru"}
    assert set(MEMORY_RECALL_RERANK_PROMPT.keys()) >= expected_locales
    for lang in expected_locales:
        rendered = (
            get_memory_recall_rerank_prompt(lang)
            .replace("{QUERY}", "X")
            .replace("{CANDIDATES}", "Y")
            .replace("{BUDGET}", "3")
        )
        assert "{QUERY}" not in rendered, lang
        assert "{CANDIDATES}" not in rendered, lang
        assert "{BUDGET}" not in rendered, lang


def test_oversample_constant_is_at_least_two():
    """COARSE_OVERSAMPLE = 1 would defeat the rerank — every coarse
    candidate would already fit the budget. Lock the lower bound so
    a future tweak can't accidentally collapse the LLM step."""
    assert COARSE_OVERSAMPLE >= 2


# ── _aload_signal_targets propagates suppress flag ──────────────────


@pytest.mark.asyncio
async def test_aload_signal_targets_carries_reflection_suppress_flag(tmp_path):
    """Codex PR-958 P2 regression: when ``_aload_signal_targets``
    materialises reflection rows for the rerank pool, it must copy
    ``suppress`` over from the source dict.  Without it, a
    suppressed reflection would survive the reranker's hard filter
    (which checks ``o.get('suppress')``) and leak back into Stage-2
    signal detection — defeating the AI-mention rate-limit gate."""
    from unittest.mock import AsyncMock, MagicMock
    from memory.facts import FactStore

    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    with patch("memory.facts.get_config_manager", return_value=cm):
        fs = FactStore()
        fs._config_manager = cm

    # Build a stub reflection engine that returns one suppressed +
    # one not-suppressed confirmed reflection.
    refl_engine = MagicMock()

    async def _fake_load(_name):
        return [
            {"id": "r_active", "text": "active observation",
             "entity": "master", "status": "confirmed",
             "suppress": False},
            {"id": "r_silent", "text": "silenced observation",
             "entity": "master", "status": "confirmed",
             "suppress": True, "suppressed_at": "2026-04-25T00:00:00"},
        ]

    refl_engine._aload_reflections_full = _fake_load

    # Persona stub returns nothing — the bug under test is reflection-side.
    persona = MagicMock()

    async def _fake_persona(_name):
        return {}

    persona.aensure_persona = _fake_persona

    pool = await fs._aload_signal_targets(
        "小天", reflection_engine=refl_engine, persona_manager=persona,
    )
    by_id = {o["id"]: o for o in pool}
    assert "reflection.r_silent" in by_id
    assert by_id["reflection.r_silent"]["suppress"] is True
    assert by_id["reflection.r_active"]["suppress"] is False

    # And the reranker's hard filter actually drops the silenced one
    # — the end-to-end check that the propagation matters.
    survivors = MemoryRecallReranker._hard_filter(pool)
    survivor_ids = {o["id"] for o in survivors}
    assert "reflection.r_active" in survivor_ids
    assert "reflection.r_silent" not in survivor_ids


@pytest.mark.asyncio
async def test_aload_signal_targets_suppress_filter_applies_when_vectors_disabled(tmp_path):
    """CodeRabbit PR-956 Major regression: an earlier shape gated the
    `aretrieve_candidates` call on `reranker._service.is_available()`
    and fell through to a bare `pool.sort(score)` when the embedding
    service was INIT / LOADING / DISABLED. The bare-sort path skipped
    `_hard_filter`, so suppressed reflections (and persona entries)
    leaked back into the Stage-2 candidate set during the warmup
    window — defeating the AI-mention rate-limit gate.

    The unification routes everything through the reranker
    regardless of vector state; this test pins that contract by
    forcing the embedding service into DISABLED and asserting the
    suppressed reflection is still dropped from the returned pool."""
    from unittest.mock import AsyncMock, MagicMock
    from memory.facts import FactStore
    from memory.embeddings import reset_embedding_service_for_tests

    reset_embedding_service_for_tests()
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    with patch("memory.facts.get_config_manager", return_value=cm):
        fs = FactStore()
        fs._config_manager = cm

    refl_engine = MagicMock()

    async def _fake_load(_name):
        return [
            {"id": "r_active", "text": "active observation",
             "entity": "master", "status": "confirmed",
             "suppress": False},
            {"id": "r_silent", "text": "silenced observation",
             "entity": "master", "status": "confirmed",
             "suppress": True, "suppressed_at": "2026-04-25T00:00:00"},
        ]

    refl_engine._aload_reflections_full = _fake_load
    persona = MagicMock()

    async def _fake_persona(_name):
        return {}

    persona.aensure_persona = _fake_persona

    # Force unavailable embedding service — would have triggered the
    # bare-sort fallback under the old shape that bypassed
    # _hard_filter.
    disabled = _FakeService(available=False)
    with patch("memory.recall.get_embedding_service", return_value=disabled):
        result = await fs._aload_signal_targets(
            "小天",
            reflection_engine=refl_engine,
            persona_manager=persona,
            new_facts=[{"id": "f1", "text": "user mentioned cats"}],
        )

    result_ids = {o["id"] for o in result}
    assert "reflection.r_active" in result_ids
    # The whole point: even with vectors DISABLED, hard_filter ran
    # and dropped the suppressed reflection.
    assert "reflection.r_silent" not in result_ids
