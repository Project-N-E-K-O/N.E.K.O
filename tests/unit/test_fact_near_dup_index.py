# -*- coding: utf-8 -*-
"""Unit tests for the FTS5 near-duplicate layer (issue #2703).

Contracts under test:

  1. Tokenization: a Chinese fact produces many tokens, not one. The old
     ``unicode61``-over-raw-text index made a whole run of CJK a single
     token, so nothing but a byte-identical query could ever retrieve it —
     and Stage-1's SHA-256 already caught those. Traditional and
     Simplified renderings of one sentence tokenize identically.
  2. Retrieval: a rephrased Chinese fact retrieves the original. This is
     the regression the issue is about; on the old code it returned
     nothing at all.
  3. Scoring: ``token_overlap`` is a 0..1 Dice score, high for rewordings
     and low for unrelated text — but it does NOT separate meaning
     ("got a cat" / "got a dog" is the highest score two facts can
     plausibly have), which is why it may not decide alone.
  4. Stage-2 policy: only token-set identity (fold + stop-name variants)
     drops a fact outright. Everything else is written AND handed to the
     LLM arbitration queue.
  5. Backfill: facts written before the index was rebuilt get indexed
     once, and the marker stops it from rescanning on every write.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.facts import FactStore
from memory.timeindex import (
    FACT_NEAR_DUP_ARBITRATE_OVERLAP,
    FACT_NEAR_DUP_IDENTICAL_OVERLAP,
    TimeIndexedMemory,
    fts_tokens,
    token_overlap,
)
from utils.cjk_fold import (
    _SIMP_FOLD_TARGET,
    _TRAD_FOLD_SOURCE,
    fold_cjk,
)


# ── 1. tokenization ──────────────────────────────────────────────────


def test_chinese_fact_is_not_one_token():
    """#2703 root cause: unicode61 indexes a run of CJK as a single token."""
    tokens = fts_tokens("用户最近养了一只猫")
    assert len(tokens) > 1
    # 2-gram + 3-gram 滑窗：9 个字 → 8 + 7 个 token。
    assert "养了" in tokens
    assert "了一只" in tokens


def test_traditional_and_simplified_tokenize_identically():
    assert fts_tokens("用戶最近養了一隻貓") == fts_tokens("用户最近养了一只猫")


def test_fold_leaves_non_cjk_alone_and_is_idempotent():
    assert fold_cjk("hello ABC 123") == "hello ABC 123"
    assert fold_cjk("") == ""
    once = fold_cjk("使用者最近養了一隻貓")
    assert fold_cjk(once) == once


def test_fold_map_columns_line_up():
    """maketrans pairs the two constants positionally — equal length or the
    whole map silently shifts by one character."""
    assert len(_TRAD_FOLD_SOURCE) == len(_SIMP_FOLD_TARGET)
    assert len(_TRAD_FOLD_SOURCE) > 1000


def test_stop_names_are_stripped_after_folding():
    """The stop-name list may be Simplified while the text is Traditional;
    folding first is what lets the strip find it."""
    assert fts_tokens("蘭蘭喜歡貓", ["兰兰"]) == fts_tokens("喜欢猫")


# ── 2/3. retrieval + scoring ─────────────────────────────────────────


def test_rewording_scores_high_and_unrelated_text_scores_zero():
    cat = fts_tokens("用户最近养了一只猫")
    assert token_overlap(cat, fts_tokens("用戶最近養了一隻貓")) == 1.0
    assert token_overlap(cat, fts_tokens("他对机器学习很感兴趣")) == 0.0
    assert token_overlap(cat, []) == 0.0


def test_overlap_does_not_separate_meaning():
    """Pinning the reason Stage-2 must not decide on its own: the highest
    scoring pair here is the one that must stay two facts."""
    cat = fts_tokens("用户最近养了一只猫")
    dog = fts_tokens("用户最近养了一只狗")
    rephrase = fts_tokens("用户的职业是程序员")
    same = fts_tokens("用户是一名程序员")
    assert token_overlap(cat, dog) > token_overlap(rephrase, same)
    # ...而那条真该合并的改写仍然够得着仲裁线。
    assert token_overlap(rephrase, same) >= FACT_NEAR_DUP_ARBITRATE_OVERLAP


def _cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    character_data = (
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    )
    cm.get_character_data = MagicMock(return_value=character_data)
    cm.aget_character_data = AsyncMock(return_value=character_data)
    return cm


@pytest.fixture
def index(tmp_path):
    cm = _cm(str(tmp_path))
    with patch("memory.timeindex.get_config_manager", return_value=cm):
        yield TimeIndexedMemory(recent_history_manager=MagicMock())


def test_reworded_chinese_fact_retrieves_the_original(index):
    """The issue in one assertion: on the old index this returned []."""
    index.index_fact("小天", "f1", "用户最近养了一只猫")
    index.index_fact("小天", "f2", "他对机器学习很感兴趣")

    hits = dict(index.search_similar_facts("小天", "用户前几天养了只猫"))
    assert "f1" in hits
    assert hits["f1"] > 0
    assert hits.get("f2", 0.0) < hits["f1"]


def test_traditional_query_retrieves_simplified_fact(index):
    index.index_fact("小天", "f1", "用户最近养了一只猫")
    hits = dict(index.search_similar_facts("小天", "用戶最近養了一隻貓"))
    assert hits["f1"] == FACT_NEAR_DUP_IDENTICAL_OVERLAP


def test_results_are_sorted_by_overlap_descending(index):
    index.index_fact("小天", "near", "用户最近养了一只猫")
    index.index_fact("小天", "far", "用户喜欢在深夜写代码")
    hits = index.search_similar_facts("小天", "用户最近养了一只猫")
    assert [fid for fid, _ in hits][0] == "near"
    assert hits == sorted(hits, key=lambda item: item[1], reverse=True)


def test_fact_id_does_not_pollute_the_candidate_set(index):
    """fact_id must be UNINDEXED: otherwise a Latin token in the query
    matches the id column of every row and the window degenerates."""
    index.index_fact("小天", "fact_20260101_deadbeef", "totally unrelated")
    assert index.search_similar_facts("小天", "some fact about work") == []


def test_query_of_only_stop_names_returns_nothing(index):
    index.index_fact("小天", "f1", "用户最近养了一只猫")
    assert index.search_similar_facts("小天", "主人") == []


def test_creating_the_v2_table_drops_the_legacy_one(index):
    from sqlalchemy import text as sql_text

    index._ensure_engine_exists("小天")
    with index.engines["小天"].connect() as conn:
        conn.execute(sql_text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts "
            "USING fts5(fact_id, content, tokenize='unicode61')"
        ))
        conn.execute(sql_text(
            "INSERT INTO facts_fts(fact_id, content) VALUES('old', '旧原文')"
        ))
        conn.commit()

    index.index_fact("小天", "f1", "用户最近养了一只猫")

    with index.engines["小天"].connect() as conn:
        remaining = conn.execute(sql_text(
            "SELECT name FROM sqlite_master WHERE name = 'facts_fts'"
        )).fetchone()
    # 隐私擦除只认新表；旧表留着就等于删掉的原文还躺在另一处。
    assert remaining is None


def test_backfill_indexes_history_once(index):
    assert index.fts_index_needs_backfill("小天") is True

    indexed = index.backfill_fact_index(
        "小天", [("f1", "用户最近养了一只猫"), ("f2", "用户喜欢喝咖啡")],
    )
    assert indexed == 2
    assert index.fts_index_needs_backfill("小天") is False
    hits = dict(index.search_similar_facts("小天", "用户前几天养了只猫"))
    assert "f1" in hits

    # 重跑不会重复插入（崩在 insert 与标记之间只赔一次重扫）。
    assert index.backfill_fact_index(
        "小天", [("f1", "用户最近养了一只猫")],
    ) == 0


# ── 4. Stage-2 policy ────────────────────────────────────────────────


class _FakeIndex:
    def __init__(self, hits=()):
        self.hits = list(hits)

    async def asearch_similar_facts(self, _name, _text, limit):
        return self.hits[:limit]

    async def aindex_fact(self, *_a, **_k):
        return None

    def fts_index_needs_backfill(self, _name):
        return False


class _PersistHarness(FactStore):
    def __init__(self, time_indexed):
        super().__init__(time_indexed_memory=time_indexed)
        self._mem: list[dict] = []

    async def aload_facts(self, _name):
        return self._mem

    async def asave_facts(self, _name):
        return None


def _harness(tmp_path, hits):
    cm = _cm(str(tmp_path))
    with patch("memory.facts.get_config_manager", return_value=cm):
        harness = _PersistHarness(_FakeIndex(hits))
    harness._config_manager = cm
    return harness


def _seed(harness, text, **extra):
    entry = {
        "id": "existing", "text": text, "importance": 7,
        "entity": "master", "hash": "seedhash",
        **extra,
    }
    harness._mem.append(entry)
    return entry


async def _persist(harness, text):
    return await harness._apersist_new_facts(
        "小天", [{"text": text, "importance": 7, "entity": "master"}],
        default_source="user_observation", semantic_dedup=True,
    )


@pytest.mark.asyncio
async def test_identical_token_set_still_drops_the_new_fact(tmp_path):
    harness = _harness(tmp_path, [("existing", FACT_NEAR_DUP_IDENTICAL_OVERLAP)])
    _seed(harness, "用户最近养了一只猫")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用戶最近養了一隻貓")

    assert created == []
    resolver.aenqueue_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_strong_overlap_writes_the_fact_and_queues_arbitration(tmp_path):
    """The cat/dog case: highest textual overlap, must stay two facts."""
    harness = _harness(tmp_path, [("existing", 0.87)])
    _seed(harness, "用户最近养了一只猫")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用户最近养了一只狗")

    assert len(created) == 1
    resolver.aenqueue_candidates.assert_awaited_once()
    name, pairs = resolver.aenqueue_candidates.await_args.args
    assert name == "小天"
    assert len(pairs) == 1
    assert pairs[0]["candidate_id"] == created[0]["id"]
    assert pairs[0]["existing_id"] == "existing"
    assert pairs[0]["text_overlap"] == 0.87
    assert pairs[0]["detector"] == "fts_near_dup"
    # cosine 不能被文字重叠冒名顶替：它会原样进仲裁 prompt。
    assert "cosine" not in pairs[0]


@pytest.mark.asyncio
async def test_weak_overlap_queues_nothing(tmp_path):
    harness = _harness(
        tmp_path, [("existing", FACT_NEAR_DUP_ARBITRATE_OVERLAP - 0.01)],
    )
    _seed(harness, "用户最近养了一只猫")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=0)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用户喜欢在深夜写代码")

    assert len(created) == 1
    resolver.aenqueue_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_absorbed_row_is_written_but_not_arbitrated(tmp_path):
    """Merging into an absorbed row would revive it out of the archive."""
    harness = _harness(tmp_path, [("existing", 0.9)])
    _seed(harness, "用户最近养了一只猫", absorbed=True)
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=0)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用户最近养了一只狗")

    assert len(created) == 1
    resolver.aenqueue_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_resolver_still_writes_both_facts(tmp_path):
    """A store without an arbitration queue keeps both facts rather than
    dropping one on textual overlap alone."""
    harness = _harness(tmp_path, [("existing", 0.87)])
    _seed(harness, "用户最近养了一只猫")

    created = await _persist(harness, "用户最近养了一只狗")

    assert len(created) == 1


@pytest.mark.asyncio
async def test_pair_is_queued_only_after_the_save_succeeds(tmp_path):
    """The queue is ids-only, so a pair naming a fact that never reached
    facts.json is a dangling reference."""
    harness = _harness(tmp_path, [("existing", 0.87)])
    _seed(harness, "用户最近养了一只猫")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    with patch.object(
        harness, "asave_facts", AsyncMock(side_effect=OSError("disk full")),
    ):
        with pytest.raises(OSError):
            await _persist(harness, "用户最近养了一只狗")

    resolver.aenqueue_candidates.assert_not_awaited()


def test_the_index_module_exposes_no_bm25_search():
    """The rename is the guard: a caller left on the old name would keep
    comparing against a negative bm25 threshold, which now means the
    opposite of what it did."""
    assert not hasattr(TimeIndexedMemory, "search_facts")
    assert not hasattr(TimeIndexedMemory, "asearch_facts")


def test_backfill_reads_archive_rows_too(tmp_path):
    """Archived rows keep blocking duplicates; leaving them out of the
    backfill would quietly change that."""
    import json

    cm = _cm(str(tmp_path))
    with patch("memory.facts.get_config_manager", return_value=cm):
        harness = _PersistHarness(_FakeIndex())
    harness._config_manager = cm

    archive_path = os.path.join(str(tmp_path), "facts_archive.json")
    with open(archive_path, "w", encoding="utf-8") as fh:
        json.dump([{"id": "arch1", "text": "群规是不剧透"}], fh)

    captured: list[list[tuple[str, str]]] = []

    class _CapturingIndex(_FakeIndex):
        def fts_index_needs_backfill(self, _name):
            return True

        async def abackfill_fact_index(self, _name, rows):
            captured.append(rows)
            return len(rows)

    harness._time_indexed = _CapturingIndex()
    with patch.object(
        harness, "_facts_archive_path", return_value=archive_path,
    ):
        import asyncio
        asyncio.run(harness._aensure_fact_index_backfilled(
            "小天", [{"id": "act1", "text": "用户最近养了一只猫"}],
        ))

    assert captured and dict(captured[0]) == {
        "act1": "用户最近养了一只猫", "arch1": "群规是不剧透",
    }
    # 第二次不再重扫。
    asyncio.run(harness._aensure_fact_index_backfilled("小天", []))
    assert len(captured) == 1


def test_a_failed_backfill_is_retried(tmp_path):
    """Recording "done" on a failure would leave the entire history out of
    the index for the rest of the process while Stage-2 looks like it works."""
    import asyncio

    cm = _cm(str(tmp_path))
    with patch("memory.facts.get_config_manager", return_value=cm):
        harness = _PersistHarness(_FakeIndex())
    harness._config_manager = cm

    attempts: list[int] = []

    class _FailingIndex(_FakeIndex):
        def fts_index_needs_backfill(self, _name):
            return True

        async def abackfill_fact_index(self, _name, rows):
            attempts.append(len(rows))
            return None if len(attempts) == 1 else len(rows)

    harness._time_indexed = _FailingIndex()
    rows = [{"id": "act1", "text": "用户最近养了一只猫"}]
    with patch.object(harness, "_facts_archive_path", return_value=""):
        asyncio.run(harness._aensure_fact_index_backfilled("小天", rows))
        assert attempts == [1]
        asyncio.run(harness._aensure_fact_index_backfilled("小天", rows))
        assert attempts == [1, 1]
        # 成功之后才停。
        asyncio.run(harness._aensure_fact_index_backfilled("小天", rows))
        assert attempts == [1, 1]
