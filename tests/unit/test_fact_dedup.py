# -*- coding: utf-8 -*-
"""Unit tests for memory.fact_dedup.FactDedupResolver — vector-aware
fact deduplication via LLM arbitration.

Covers four contracts:

  1. ``detect_candidates`` returns entity-scoped, absorbed-aware,
     cosine-thresholded (candidate, existing) pairs and respects the
     per-fact cap so a pathological row can't flood the queue.
  2. ``aenqueue_candidates`` deduplicates by (candidate_id, existing_id)
     so an oscillating worker (e.g. re-embed under a new model_id)
     can't grow the queue unboundedly with the same pair.
  3. ``aresolve`` translates LLM ``merge`` / ``replace`` / ``keep_both``
     decisions into facts.json mutations correctly: merge bumps
     importance + records candidate id under merged_from_ids, replace
     promotes the candidate and carries provenance forward, keep_both
     leaves both rows untouched.
  4. The whole pipeline degrades correctly when the LLM call fails —
     queue stays intact for the next tick, no facts are lost.

We do NOT exercise the real LLM. The resolve-path tests stub
`utils.llm_client.create_chat_llm` the same way PR #941's
test_persona_version_history.py does."""
from __future__ import annotations

import json

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.fact_dedup import (
    FACT_DEDUP_BATCH_LIMIT,
    FACT_DEDUP_COSINE_THRESHOLD,
    FACT_DEDUP_PAIRS_PER_NEW,
    FactDedupResolver,
)


# ── helpers ──────────────────────────────────────────────────────────


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    return cm


def _install_resolver(tmpdir: str):
    """Build a FactStore + FactDedupResolver bound to ``tmpdir`` so
    facts.json and facts_pending_dedup.json round-trip through real
    file I/O — that's the contract the queue depends on for crash-
    recovery."""
    from memory.facts import FactStore

    cm = _mock_cm(tmpdir)
    with patch("memory.facts.get_config_manager", return_value=cm):
        fs = FactStore()
        fs._config_manager = cm
    resolver = FactDedupResolver(fs)
    resolver._config_manager = cm
    return fs, resolver


def _fact(fid: str, text: str, *, entity: str = "master",
          embedding: list[float] | None = None,
          importance: int = 5,
          absorbed: bool = False,
          merged_from_ids: list[str] | None = None) -> dict:
    return {
        "id": fid,
        "text": text,
        "entity": entity,
        "importance": importance,
        "tags": [],
        "hash": fid + "h",
        "created_at": "2026-04-25T10:00:00",
        "absorbed": absorbed,
        "merged_from_ids": merged_from_ids or [],
        "embedding": list(embedding) if embedding is not None else None,
        "embedding_text_sha256": "sha-" + fid if embedding else None,
        "embedding_model_id": "jina-v5-nano-128d-int8" if embedding else None,
    }


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


# ── detect_candidates ────────────────────────────────────────────────


def test_detect_candidates_emits_pair_above_threshold():
    """The bread-and-butter case: two near-identical embeddings under
    the same entity surface as a candidate pair."""
    a_vec = [1.0, 0.0, 0.0, 0.0]
    b_vec = [0.99, 0.05, 0.05, 0.05]
    facts = [
        _fact("f1", "主人喜欢猫", embedding=a_vec),
        _fact("f2", "主人对猫咪很感兴趣", embedding=b_vec),
    ]
    pairs = FactDedupResolver.detect_candidates(facts)
    assert len(pairs) >= 1
    pair = next(p for p in pairs if p["candidate_id"] == "f1")
    assert pair["existing_id"] == "f2"
    assert pair["entity"] == "master"
    assert pair["cosine"] > FACT_DEDUP_COSINE_THRESHOLD


def test_detect_candidates_below_threshold_no_pair():
    """Cosine < threshold ⇒ no pair. Keeps "主人喜欢猫" / "主人讨厌猫"
    style polarity flips out of the queue (they ride opposite halves
    of the embedding space ≈0.78 in practice)."""
    a_vec = [1.0, 0.0, 0.0]
    b_vec = [0.0, 1.0, 0.0]
    facts = [
        _fact("f1", "主人喜欢猫", embedding=a_vec),
        _fact("f2", "主人讨厌猫", embedding=b_vec),
    ]
    assert FactDedupResolver.detect_candidates(facts) == []


def test_detect_candidates_respects_entity_scope():
    """master + relationship entries don't collide even with identical
    embeddings — cross-entity dedup is too risky to defer to vectors."""
    same_vec = [1.0, 0.0, 0.0]
    facts = [
        _fact("f1", "主人喜欢猫", entity="master", embedding=same_vec),
        _fact("f2", "他们关系融洽", entity="relationship", embedding=same_vec),
    ]
    assert FactDedupResolver.detect_candidates(facts) == []


def test_detect_candidates_skips_absorbed_existing():
    """An absorbed fact has already been folded into a reflection; we
    don't want to resurrect it via a paraphrase merge."""
    same_vec = [1.0, 0.0, 0.0]
    facts = [
        _fact("f1", "新表述", embedding=same_vec),
        _fact("f2", "旧表述", embedding=same_vec, absorbed=True),
    ]
    assert FactDedupResolver.detect_candidates(facts) == []


def test_detect_candidates_skips_self():
    """A row never collides with itself (cosine = 1 trivially)."""
    facts = [_fact("f1", "x", embedding=[1.0, 0.0])]
    assert FactDedupResolver.detect_candidates(facts) == []


def test_detect_candidates_only_for_ids_filters_candidate_side():
    """only_for_ids constrains the *candidate* (newer) side so the
    worker doesn't repeatedly scan the entire history on every sweep
    — only the rows it just embedded count as candidates."""
    same_vec = [1.0, 0.0, 0.0]
    facts = [
        _fact("f1", "old", embedding=same_vec),
        _fact("f2", "old paraphrase", embedding=same_vec),
        _fact("f3", "new", embedding=same_vec),
    ]
    # Only f3 is "new"; f1/f2 should never appear as candidate side.
    pairs = FactDedupResolver.detect_candidates(
        facts, only_for_ids={"f3"},
    )
    assert all(p["candidate_id"] == "f3" for p in pairs)


def test_detect_candidates_per_fact_limit_caps_collisions():
    """A pathological row near 5 existing rows must not produce 5
    pairs — the cap keeps the queue interpretable."""
    same_vec = [1.0, 0.0, 0.0]
    facts = [_fact("f0", "candidate", embedding=same_vec)]
    for i in range(8):
        facts.append(_fact(f"e{i}", f"existing {i}", embedding=same_vec))
    pairs = FactDedupResolver.detect_candidates(
        facts, only_for_ids={"f0"},
    )
    assert len(pairs) == FACT_DEDUP_PAIRS_PER_NEW


def test_detect_candidates_skips_rows_without_embedding():
    """A row whose embedding hasn't been computed yet can't participate
    — the warmup worker will retry on its next sweep."""
    facts = [
        _fact("f1", "x", embedding=[1.0, 0.0]),
        _fact("f2", "y", embedding=None),
    ]
    assert FactDedupResolver.detect_candidates(facts) == []


# ── aenqueue_candidates ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aenqueue_candidates_appends_and_persists(tmp_path):
    """Append round-trips through atomic_write_json + read_json so
    crash-recovery works (queue file IS the source of truth)."""
    _, resolver = _install_resolver(str(tmp_path))
    appended = await resolver.aenqueue_candidates("小天", [{
        "candidate_id": "f1", "existing_id": "f2",
        "candidate_text": "a", "existing_text": "b",
        "entity": "master", "cosine": 0.91,
    }])
    assert appended == 1
    pending = await resolver.aload_pending("小天")
    assert len(pending) == 1
    assert pending[0]["candidate_id"] == "f1"
    assert pending[0]["cosine"] == pytest.approx(0.91)
    assert pending[0]["queued_at"]


@pytest.mark.asyncio
async def test_aenqueue_dedups_same_pair_across_calls(tmp_path):
    """Re-enqueue of the same (candidate_id, existing_id) pair must
    no-op — otherwise an oscillating worker (re-embed under new
    model_id) would grow the queue unboundedly with duplicates."""
    _, resolver = _install_resolver(str(tmp_path))
    pair = {
        "candidate_id": "f1", "existing_id": "f2",
        "candidate_text": "a", "existing_text": "b",
        "entity": "master", "cosine": 0.91,
    }
    await resolver.aenqueue_candidates("小天", [pair])
    appended2 = await resolver.aenqueue_candidates("小天", [pair])
    assert appended2 == 0
    pending = await resolver.aload_pending("小天")
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_aenqueue_skips_rows_with_missing_ids(tmp_path):
    """Defensive: malformed pair (no candidate_id) shouldn't pollute
    the queue or crash."""
    _, resolver = _install_resolver(str(tmp_path))
    appended = await resolver.aenqueue_candidates("小天", [
        {"candidate_id": None, "existing_id": "f2"},
        {"candidate_id": "f1", "existing_id": None},
    ])
    assert appended == 0
    assert await resolver.aload_pending("小天") == []


# ── aresolve: action handling ────────────────────────────────────────


async def _seed_facts(fs, name: str, facts: list[dict]) -> None:
    """Write facts straight to the in-memory store and flush to disk."""
    fs._facts[name] = list(facts)
    await fs.asave_facts(name)


@pytest.mark.asyncio
async def test_aresolve_merge_drops_candidate_and_bumps_importance(tmp_path):
    """merge ⇒ keep existing, drop candidate, importance += 1 (capped at 10),
    candidate id appended to existing.merged_from_ids."""
    fs, resolver = _install_resolver(str(tmp_path))
    cand = _fact("c1", "对猫咪很感兴趣", embedding=[1.0, 0.0])
    existing = _fact("e1", "主人喜欢猫", embedding=[0.99, 0.05], importance=4)
    await _seed_facts(fs, "小天", [cand, existing])
    await resolver.aenqueue_candidates("小天", [{
        "candidate_id": "c1", "existing_id": "e1",
        "candidate_text": cand["text"], "existing_text": existing["text"],
        "entity": "master", "cosine": 0.99,
    }])
    fake_llm = _make_llm_mock([{"index": 0, "action": "merge"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await resolver.aresolve("小天")
    assert resolved == 1
    facts = await fs.aload_facts("小天")
    ids = {f["id"] for f in facts}
    assert ids == {"e1"}  # candidate removed
    survivor = next(f for f in facts if f["id"] == "e1")
    assert survivor["importance"] == 5  # 4 + 1
    assert "c1" in (survivor.get("merged_from_ids") or [])
    # Queue is empty after resolve.
    assert await resolver.aload_pending("小天") == []


@pytest.mark.asyncio
async def test_aresolve_merge_caps_importance_at_ten(tmp_path):
    """A parade of paraphrase merges shouldn't grow importance above
    the documented 1..10 range — same clamp as _apersist_new_facts."""
    fs, resolver = _install_resolver(str(tmp_path))
    cand = _fact("c1", "x", embedding=[1.0, 0.0])
    existing = _fact("e1", "y", embedding=[0.99, 0.05], importance=10)
    await _seed_facts(fs, "小天", [cand, existing])
    await resolver.aenqueue_candidates("小天", [{
        "candidate_id": "c1", "existing_id": "e1",
        "candidate_text": "x", "existing_text": "y",
        "entity": "master", "cosine": 0.99,
    }])
    fake_llm = _make_llm_mock([{"index": 0, "action": "merge"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await resolver.aresolve("小天")
    survivor = next(f for f in await fs.aload_facts("小天") if f["id"] == "e1")
    assert survivor["importance"] == 10


@pytest.mark.asyncio
async def test_aresolve_replace_keeps_candidate_and_carries_provenance(tmp_path):
    """replace ⇒ keep candidate, drop existing. Existing's
    merged_from_ids chain transfers to candidate so we don't lose the
    earlier paraphrase trail."""
    fs, resolver = _install_resolver(str(tmp_path))
    cand = _fact("c1", "新表述", embedding=[1.0, 0.0], importance=6)
    existing = _fact(
        "e1", "旧表述", embedding=[0.99, 0.05], importance=4,
        merged_from_ids=["older_id"],
    )
    await _seed_facts(fs, "小天", [cand, existing])
    await resolver.aenqueue_candidates("小天", [{
        "candidate_id": "c1", "existing_id": "e1",
        "candidate_text": "新表述", "existing_text": "旧表述",
        "entity": "master", "cosine": 0.99,
    }])
    fake_llm = _make_llm_mock([{"index": 0, "action": "replace"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await resolver.aresolve("小天")
    assert resolved == 1
    facts = await fs.aload_facts("小天")
    assert {f["id"] for f in facts} == {"c1"}
    survivor = next(f for f in facts if f["id"] == "c1")
    # Importance: max of the two (don't silently demote a strong row)
    assert survivor["importance"] == 6
    chain = set(survivor.get("merged_from_ids") or [])
    assert "older_id" in chain
    assert "e1" in chain


@pytest.mark.asyncio
async def test_aresolve_keep_both_leaves_facts_untouched(tmp_path):
    """keep_both ⇒ both rows survive intact, queue cleared.
    This is the safety-net branch for "high cosine but actually
    different" decisions."""
    fs, resolver = _install_resolver(str(tmp_path))
    cand = _fact("c1", "主人喜欢猫", embedding=[1.0, 0.0], importance=5)
    existing = _fact("e1", "主人讨厌狗", embedding=[0.95, 0.05], importance=3)
    await _seed_facts(fs, "小天", [cand, existing])
    await resolver.aenqueue_candidates("小天", [{
        "candidate_id": "c1", "existing_id": "e1",
        "candidate_text": cand["text"], "existing_text": existing["text"],
        "entity": "master", "cosine": 0.99,
    }])
    fake_llm = _make_llm_mock([{"index": 0, "action": "keep_both"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await resolver.aresolve("小天")
    assert resolved == 1
    facts = await fs.aload_facts("小天")
    assert {f["id"] for f in facts} == {"c1", "e1"}
    # Importance unchanged on both
    assert next(f for f in facts if f["id"] == "c1")["importance"] == 5
    assert next(f for f in facts if f["id"] == "e1")["importance"] == 3
    # Queue is consumed even though no mutation happened
    assert await resolver.aload_pending("小天") == []


@pytest.mark.asyncio
async def test_aresolve_skips_decision_for_disappeared_row(tmp_path):
    """If a fact in the queue has been deleted between enqueue and
    resolve (e.g. concurrent absorbed-archive sweep), the decision
    silently no-ops — better than crashing the whole batch."""
    fs, resolver = _install_resolver(str(tmp_path))
    # Only the existing survives; candidate "c1" is absent from disk.
    existing = _fact("e1", "x", embedding=[1.0, 0.0], importance=5)
    await _seed_facts(fs, "小天", [existing])
    await resolver.aenqueue_candidates("小天", [{
        "candidate_id": "c1", "existing_id": "e1",
        "candidate_text": "x", "existing_text": "x",
        "entity": "master", "cosine": 0.99,
    }])
    fake_llm = _make_llm_mock([{"index": 0, "action": "merge"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        # Resolved count is 0 because the merge couldn't apply (cand missing).
        resolved = await resolver.aresolve("小天")
    assert resolved == 0
    survivor = next(f for f in await fs.aload_facts("小天") if f["id"] == "e1")
    assert survivor["importance"] == 5  # untouched
    # Queue entry IS still removed — staleness shouldn't keep it
    # blocking the next batch.
    pending = await resolver.aload_pending("小天")
    assert all(p["candidate_id"] != "c1" for p in pending)


# ── aresolve: failure modes ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_aresolve_llm_failure_preserves_queue(tmp_path):
    """LLM call raises ⇒ queue is intact for the next tick. Losing
    pending dedup work is worse than skipping a round."""
    fs, resolver = _install_resolver(str(tmp_path))
    cand = _fact("c1", "x", embedding=[1.0, 0.0])
    existing = _fact("e1", "y", embedding=[0.99, 0.05])
    await _seed_facts(fs, "小天", [cand, existing])
    await resolver.aenqueue_candidates("小天", [{
        "candidate_id": "c1", "existing_id": "e1",
        "candidate_text": "x", "existing_text": "y",
        "entity": "master", "cosine": 0.99,
    }])

    class _BoomLLM:
        async def ainvoke(self, *_a, **_k):
            raise RuntimeError("simulated network failure")

        async def aclose(self):
            return None

    with patch("utils.llm_client.create_chat_llm", return_value=_BoomLLM()):
        resolved = await resolver.aresolve("小天")
    assert resolved == 0
    # Both facts still present, queue still has the pair.
    assert {f["id"] for f in await fs.aload_facts("小天")} == {"c1", "e1"}
    pending = await resolver.aload_pending("小天")
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_aresolve_empty_queue_is_noop(tmp_path):
    """No pending pairs ⇒ early-out without an LLM call. Critical for
    the idle loop to be cheap when nothing's queued."""
    fs, resolver = _install_resolver(str(tmp_path))

    # Patch create_chat_llm to a mock that records calls; assert it's
    # never invoked because aresolve must early-out before calling.
    create_llm = MagicMock()
    with patch("utils.llm_client.create_chat_llm", create_llm):
        resolved = await resolver.aresolve("小天")
    assert resolved == 0
    assert create_llm.call_count == 0


def test_fact_dedup_prompt_has_all_five_locales_with_placeholders():
    """The prompt is sent to a multilingual model — all five locales
    must be present and each must include both placeholders the
    resolver substitutes (PAIRS, COUNT). A missing locale would silently
    fall back to zh via _loc; a missing placeholder would let the LLM
    see literal {PAIRS} / {COUNT} text and produce garbage."""
    from config.prompts_memory import FACT_DEDUP_PROMPT, get_fact_dedup_prompt
    expected_locales = {"zh", "en", "ja", "ko", "ru"}
    assert set(FACT_DEDUP_PROMPT.keys()) >= expected_locales
    for lang in expected_locales:
        rendered = (
            get_fact_dedup_prompt(lang)
            .replace("{PAIRS}", "X")
            .replace("{COUNT}", "1")
        )
        # No leftover unsubstituted placeholders.
        assert "{PAIRS}" not in rendered, lang
        assert "{COUNT}" not in rendered, lang


@pytest.mark.asyncio
async def test_aresolve_batch_limit_caps_in_flight_pairs(tmp_path):
    """Resolve only processes BATCH_LIMIT items per call so the LLM
    prompt stays within sane bounds; remainder waits for next tick."""
    fs, resolver = _install_resolver(str(tmp_path))
    facts = []
    pending_pairs = []
    for i in range(FACT_DEDUP_BATCH_LIMIT + 5):
        facts.append(_fact(f"c{i}", f"cand {i}", embedding=[1.0, 0.0]))
        facts.append(_fact(f"e{i}", f"exist {i}", embedding=[1.0, 0.0]))
        pending_pairs.append({
            "candidate_id": f"c{i}", "existing_id": f"e{i}",
            "candidate_text": "x", "existing_text": "y",
            "entity": "master", "cosine": 0.99,
        })
    await _seed_facts(fs, "小天", facts)
    await resolver.aenqueue_candidates("小天", pending_pairs)
    # LLM responds keep_both for every batch item — easiest
    # decision with no facts.json mutations to assert against.
    response = [
        {"index": i, "action": "keep_both"}
        for i in range(FACT_DEDUP_BATCH_LIMIT)
    ]
    fake_llm = _make_llm_mock(response)
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await resolver.aresolve("小天")
    assert resolved == FACT_DEDUP_BATCH_LIMIT
    pending = await resolver.aload_pending("小天")
    assert len(pending) == 5  # remaining queued pairs

    # Second tick clears the rest.
    response2 = [
        {"index": i, "action": "keep_both"} for i in range(5)
    ]
    fake_llm2 = _make_llm_mock(response2)
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm2):
        resolved2 = await resolver.aresolve("小天")
    assert resolved2 == 5
    assert await resolver.aload_pending("小天") == []
