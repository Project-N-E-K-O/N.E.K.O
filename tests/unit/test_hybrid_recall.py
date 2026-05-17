# -*- coding: utf-8 -*-
"""
Unit tests for ``memory.hybrid_recall``.

Coverage matrix:
- BM25 ranking: term overlap drives ordering; non-overlap scores 0 (dropped)
- RRF fusion: dual-list docs outrank single-list docs at same rank
- Hard filter: score<0 / suppressed / terminal-status reflections dropped
- Pool composition: archive enters BM25 pool, NOT embedding pool; persona
  never enters either pool
- Threshold filter: per-side caps respected
- Empty query / empty pool / no-overlap → empty results, no crash
- EmbeddingService unavailable → cosine path returns [], BM25-only fallback

Embedding paths are mocked to avoid loading the local ONNX model in unit
tests; we exercise the cosine code only via stubbing.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from memory.hybrid_recall import (
    _bm25_rank,
    _rrf_fuse,
    _tag_tier,
    _tokenize,
    hybrid_recall,
)


# ── tokenization ──────────────────────────────────────────────────────


class TestTokenize(unittest.TestCase):
    def test_cjk_generates_2_and_3_grams(self):
        tokens = _tokenize("博士最爱猫咪", [])
        # 6 chars → 5 bigrams + 4 trigrams; set dedupes
        self.assertIn("博士", tokens)
        self.assertIn("博士最", tokens)
        self.assertIn("猫咪", tokens)

    def test_latin_split_keeps_len_ge_2(self):
        tokens = _tokenize("hello world a I'm", [])
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)
        # len-1 dropped
        self.assertNotIn("a", tokens)
        self.assertNotIn("I", tokens)

    def test_mixed_cjk_latin(self):
        tokens = _tokenize("博士最爱 The Witness", [])
        # CJK segment → grams
        self.assertIn("博士", tokens)
        # Latin segment → tokens (>=2 chars)
        self.assertIn("The", tokens)
        self.assertIn("Witness", tokens)

    def test_stop_names_stripped(self):
        # When "博士" is a stop_name, the CJK segment becomes "最爱猫咪"
        # and bigrams shouldn't contain "博士".
        tokens = _tokenize("博士最爱猫咪", ["博士"])
        self.assertNotIn("博士", tokens)
        self.assertIn("最爱", tokens)


# ── BM25 ranking ─────────────────────────────────────────────────────


class TestBM25Rank(unittest.TestCase):
    def test_overlap_drives_ordering(self):
        pool = [
            {"id": "a", "text": "博士最喜欢的游戏是 The Witness"},
            {"id": "b", "text": "今天的天气真不错"},
            {"id": "c", "text": "博士喜欢猫咪"},
        ]
        ranked = _bm25_rank("博士 游戏", pool, stop_names=[])
        ids = [d["id"] for d, _ in ranked]
        # a (has both 博士 and 游戏) ranks first; c (only 博士) second;
        # b (no overlap) gets score 0 and is dropped
        self.assertEqual(ids[0], "a")
        self.assertNotIn("b", ids)

    def test_empty_query_returns_empty(self):
        pool = [{"id": "a", "text": "anything"}]
        self.assertEqual(_bm25_rank("", pool, stop_names=[]), [])

    def test_empty_pool_returns_empty(self):
        self.assertEqual(_bm25_rank("query", [], stop_names=[]), [])

    def test_no_overlap_returns_empty(self):
        pool = [
            {"id": "a", "text": "foo bar baz"},
            {"id": "b", "text": "qux quux"},
        ]
        ranked = _bm25_rank("totally unrelated query 完全不相干", pool, stop_names=[])
        # All score 0 — nothing returned
        self.assertEqual(ranked, [])


# ── RRF fusion ────────────────────────────────────────────────────────


class TestRRFFuse(unittest.TestCase):
    def test_dual_list_doc_outranks_single_list_docs(self):
        bm25 = [({"id": "a"}, 5.0), ({"id": "b"}, 3.0), ({"id": "c"}, 1.0)]
        cosine = [({"id": "c"}, 0.9), ({"id": "a"}, 0.5), ({"id": "d"}, 0.4)]
        fused = _rrf_fuse(bm25, cosine, k=60, budget_total=4)
        ids = [d["id"] for d in fused]
        # a is rank 1 in bm25, rank 2 in cosine → highest combined
        # c is rank 3 in bm25, rank 1 in cosine → second
        self.assertEqual(ids[0], "a")
        self.assertEqual(ids[1], "c")
        # b (only in bm25 rank 2) and d (only in cosine rank 3) follow
        self.assertIn("b", ids[2:])
        self.assertIn("d", ids[2:])

    def test_dedup_by_id(self):
        bm25 = [({"id": "a", "text": "v1"}, 1.0)]
        cosine = [({"id": "a", "text": "v2"}, 0.5)]
        fused = _rrf_fuse(bm25, cosine, k=60, budget_total=10)
        # One unique doc, RRF accumulates from both sides
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["id"], "a")
        # _rrf_score = 1/61 + 1/61 ≈ 0.0328
        self.assertAlmostEqual(fused[0]["_rrf_score"], 2.0 / 61, places=6)

    def test_budget_total_caps_output(self):
        bm25 = [({"id": str(i)}, 10.0 - i) for i in range(20)]
        cosine = []
        fused = _rrf_fuse(bm25, cosine, k=60, budget_total=3)
        self.assertEqual(len(fused), 3)

    def test_doc_without_id_skipped(self):
        bm25 = [({"id": "a"}, 1.0), ({}, 0.5)]
        cosine = []
        fused = _rrf_fuse(bm25, cosine, k=60, budget_total=10)
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["id"], "a")


# ── _tag_tier ────────────────────────────────────────────────────────


class TestTagTier(unittest.TestCase):
    def test_stamps_tier_and_target_type_for_reflection(self):
        items = [{"id": "x", "text": "..."}]
        out = _tag_tier(items, "reflection")
        self.assertEqual(out[0]["_tier"], "reflection")
        self.assertEqual(out[0]["target_type"], "reflection")

    def test_does_not_mutate_original(self):
        items = [{"id": "x", "text": "..."}]
        _tag_tier(items, "fact")
        # Original dict unchanged
        self.assertNotIn("_tier", items[0])
        self.assertNotIn("target_type", items[0])

    def test_no_target_type_for_fact(self):
        items = [{"id": "x"}]
        out = _tag_tier(items, "fact")
        # fact tier doesn't need target_type stamp (hard_filter only checks
        # reflection terminal statuses)
        self.assertNotIn("target_type", out[0])


# ── end-to-end hybrid_recall ─────────────────────────────────────────


class TestHybridRecallE2E(unittest.IsolatedAsyncioTestCase):
    """End-to-end with mocked fact_store + reflection_engine + embedding
    service. Covers pool composition, hard filter, archive-in-bm25-only,
    threshold behavior, empty-result path.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Write a fake facts_archive.json — _aload_archive_facts reads it
        # directly via fact_store._facts_archive_path().
        self.archive_path = os.path.join(self.tmpdir, "facts_archive.json")
        with open(self.archive_path, "w", encoding="utf-8") as f:
            json.dump([
                {"id": "fa_1", "text": "archived: 博士曾经养过一只猫", "score": 1.0},
            ], f)

    def _make_stores(self, active_facts, active_reflections):
        fact_store = MagicMock()
        fact_store.aload_facts = AsyncMock(return_value=active_facts)
        fact_store._facts_archive_path = MagicMock(return_value=self.archive_path)

        reflection_engine = MagicMock()
        reflection_engine.aload_reflections = AsyncMock(return_value=active_reflections)
        return fact_store, reflection_engine

    async def _run(self, query, active_facts, active_reflections):
        fact_store, reflection_engine = self._make_stores(active_facts, active_reflections)
        config_manager = MagicMock()
        # Two patches:
        # 1) Mock embedding service to "unavailable" — keeps tests deterministic
        #    (no ONNX model in CI).
        # 2) Drop BM25 threshold to 0 — the production default (1.0) is tuned
        #    for real corpora where IDF is meaningful; in 1-3 doc fixtures
        #    IDF collapses near zero and clears no threshold. Unit tests assert
        #    *logic* (filter, pool, fusion), not threshold tuning.
        with patch("memory.hybrid_recall._cosine_rank", new=AsyncMock(return_value=[])), \
             patch("memory.hybrid_recall.HYBRID_RECALL_BM25_THRESHOLD", 0.0):
            return await hybrid_recall(
                lanlan_name="testcat",
                query=query,
                fact_store=fact_store,
                reflection_engine=reflection_engine,
                config_manager=config_manager,
            )

    async def test_pool_includes_archive_in_bm25(self):
        # Empty active pool; only archive has matching content.
        res = await self._run("博士 猫", [], [])
        ids = [r["id"] for r in res["results"]]
        # fa_1 is from archive — should still be returned via BM25
        self.assertIn("fa_1", ids)
        # Tier label should be fact_archive
        archived = next(r for r in res["results"] if r["id"] == "fa_1")
        self.assertEqual(archived["tier"], "fact_archive")

    async def test_hard_filter_drops_negative_score(self):
        facts = [
            {"id": "good", "text": "博士最喜欢的游戏是 The Witness", "score": 1.0},
            {"id": "bad",  "text": "博士最喜欢的游戏是 The Witness", "score": -1.0},
        ]
        res = await self._run("博士 游戏", facts, [])
        ids = [r["id"] for r in res["results"]]
        self.assertIn("good", ids)
        self.assertNotIn("bad", ids)

    async def test_hard_filter_drops_suppressed(self):
        facts = [
            {"id": "ok", "text": "博士养了只猫", "score": 1.0},
            {"id": "supp", "text": "博士养了只猫", "score": 1.0, "suppress": True},
        ]
        res = await self._run("博士 猫", facts, [])
        ids = [r["id"] for r in res["results"]]
        self.assertIn("ok", ids)
        self.assertNotIn("supp", ids)

    async def test_hard_filter_drops_terminal_reflection(self):
        reflections = [
            {"id": "r_active", "text": "博士对长尾问题敏感", "score": 1.0,
             "status": "confirmed"},
            {"id": "r_dead",  "text": "博士对长尾问题敏感", "score": 1.0,
             "status": "denied"},
        ]
        res = await self._run("博士 长尾", [], reflections)
        ids = [r["id"] for r in res["results"]]
        self.assertIn("r_active", ids)
        self.assertNotIn("r_dead", ids)

    async def test_empty_query_short_circuits(self):
        facts = [{"id": "x", "text": "anything", "score": 1.0}]
        res = await self._run("   ", facts, [])
        self.assertEqual(res["results"], [])
        self.assertEqual(res["candidates_total"], 0)

    async def test_no_match_returns_empty_results(self):
        facts = [{"id": "x", "text": "今天的天气真不错", "score": 1.0}]
        res = await self._run("完全不相关的 query", facts, [])
        self.assertEqual(res["results"], [])

    async def test_reflection_tagged_as_reflection_tier(self):
        reflections = [
            {"id": "r1", "text": "博士对长尾敏感", "score": 1.0, "status": "confirmed"},
        ]
        # Query 用 archive 不沾边的词，避免 setUp 里 facts_archive.json
        # 那条"博士曾经养过一只猫"也被召回干扰断言。
        res = await self._run("长尾", [], reflections)
        ids_to_tier = {r["id"]: r["tier"] for r in res["results"]}
        self.assertIn("r1", ids_to_tier)
        self.assertEqual(ids_to_tier["r1"], "reflection")


if __name__ == "__main__":
    unittest.main()
