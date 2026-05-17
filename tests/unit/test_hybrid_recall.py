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

    def test_tokenize_coerces_non_string_text(self):
        """Regression for codex review (3rd round): normal-path _tokenize 之前
        漏了 str() coerce，遇到 malformed entry 里 text=list/int 等 truthy
        non-string 时，``_SPLIT_RE.split`` 抛 TypeError 把整条 hybrid_recall
        abort（应只 skip 单行）。"""
        # 不该抛任何异常，return [] (list 走 str() 后变 "[1, 2, 3]" → 一个 Latin token)
        result = _tokenize([1, 2, 3], [])
        self.assertIsInstance(result, list)
        # 应不挂；具体输出无所谓
        result = _tokenize(12345, [])
        self.assertIsInstance(result, list)
        # None 早就 OK
        self.assertEqual(_tokenize(None, []), [])

    def test_tf_preserved_so_heavy_repeat_outranks_brief(self):
        """Regression for codex review #1 (commit fd2b75fc4 之前)：
        ``_extract_keywords`` 返回 set，单 doc 内重复 token 被 dedupe，
        BM25 的 TF 信号死掉。修正后 ``_tokenize`` 返回 list 保留 multiplicity，
        同一 term 出现 N 次的 doc 应当显著高于只出现 1 次的 doc。"""
        pool = [
            {"id": "heavy", "text": "博士博士博士博士博士最爱博士的游戏"},
            {"id": "brief", "text": "今天博士跟我说了别的事"},
        ]
        ranked = _bm25_rank("博士", pool, stop_names=[])
        ids = [d["id"] for d, _ in ranked]
        # heavy 出现 "博士" 多次 → BM25 TF 项给高分；brief 只出现 1 次
        # → 同样的 IDF 但低 TF。heavy 必须排第一，分数也得明显更高。
        self.assertEqual(ids[0], "heavy")
        heavy_score = next(s for d, s in ranked if d["id"] == "heavy")
        brief_score = next(s for d, s in ranked if d["id"] == "brief")
        self.assertGreater(heavy_score, brief_score * 1.3,
                           f"TF 应放大 heavy 优势：heavy={heavy_score:.3f} brief={brief_score:.3f}")


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

    def test_skip_non_dict_entries(self):
        """Regression for codex review on commit d3880f9c9：facts.json
        如果混进 non-dict 行（manual edit / 老格式 / 迁移 bug），
        ``dict(it)`` 会 TypeError/ValueError 把整个 _tag_tier 挂掉，
        升级成 whole-query 失败。修正后单条 skip，其余继续。"""
        items = [
            {"id": "good", "text": "valid entry"},
            "this is a malformed string row",  # 非 dict
            ["nested", "list", "row"],         # 非 dict
            12345,                              # 非 dict
            {"id": "also_good", "text": "another valid entry"},
        ]
        out = _tag_tier(items, "fact")
        # 两条好 entry 都该出来，三条坏 entry 被 skip
        self.assertEqual(len(out), 2)
        self.assertEqual({d["id"] for d in out}, {"good", "also_good"})


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

    async def test_small_pool_exact_match_clears_production_threshold(self):
        """Regression for codex P1 on commit ef81ec41a: 之前 BM25 阈值定 1.0，
        但 Okapi 公式在小 pool 下 IDF 系数本身就矮（单 doc IDF ≈ 0.288），
        max score 也就 ~0.72 → 永远过不去 1.0 → 新用户 / 小语料下 BM25
        兜底完全死掉。降到 0.1 后 single-doc exact match 能正常召回。

        本测试**不 patch threshold**，用生产值（0.1）跑，验真实命中。
        """
        # 单 fact，query 完全命中：阈值 0.1 必须 clear
        facts = [{"id": "only_one", "text": "博士最喜欢的游戏是 The Witness",
                  "score": 1.0}]
        fact_store, reflection_engine = self._make_stores(facts, [])
        config_manager = MagicMock()
        # 关掉 cosine（mock 成 unavailable），证 BM25-only 路径能出结果
        with patch("memory.hybrid_recall._cosine_rank", new=AsyncMock(return_value=[])):
            res = await __import__("memory.hybrid_recall", fromlist=["hybrid_recall"]).hybrid_recall(
                lanlan_name="testcat",
                query="博士 游戏",
                fact_store=fact_store,
                reflection_engine=reflection_engine,
                config_manager=config_manager,
            )
        ids = [r["id"] for r in res["results"]]
        self.assertIn("only_one", ids,
                      "single-doc exact match should clear production BM25 threshold 0.1")

    async def test_malformed_entries_dont_kill_whole_query(self):
        """Regression for codex review on commit 47d0d191f: 单条 malformed
        entry (text 是 list / score 是 string 等) 不该带挂整个 hybrid_recall
        → 应只 skip 那一行，其余好的 entry 继续返回。

        修在 ``MemoryRecallReranker._hard_filter`` 加 try/except per-entry。
        """
        facts = [
            # 正常 entry
            {"id": "good_1", "text": "博士最喜欢的游戏是 The Witness", "score": 1.0},
            # 坏 entry: text 是 list（manual edit / 老格式残留）
            {"id": "bad_text", "text": ["this", "is", "wrong"], "score": 1.0},
            # 坏 entry: score 是 string（无法和 0 比较）
            {"id": "bad_score", "text": "博士的游戏", "score": "high"},
            # 正常 entry
            {"id": "good_2", "text": "博士最爱的游戏 The Witness", "score": 1.0},
        ]
        # 不该抛任何异常，good_1 / good_2 都应该被召回
        res = await self._run("博士 游戏", facts, [])
        ids = [r["id"] for r in res["results"]]
        self.assertIn("good_1", ids)
        self.assertIn("good_2", ids)
        # 坏 entry 自然不出现
        self.assertNotIn("bad_text", ids)
        self.assertNotIn("bad_score", ids)

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
