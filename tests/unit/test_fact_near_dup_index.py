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
    normalized_identity,
    token_overlap,
)
from memory.script_fold import fold_script


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
    """``memory.script_fold`` is shared with the recall side (#2584); the
    fold's own invariants are pinned there. What matters here is only that
    the dedup side folds at all, on both index and query."""
    assert fold_script("hello ABC 123") == "hello ABC 123"
    once = fold_script("使用者最近養了一隻貓")
    assert fold_script(once) == once


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


def test_clause_swap_shares_a_token_set_but_not_an_identity():
    """Codex P1: n-gram sets discard order, so two opposite statements can
    reach overlap 1.0. Only the order-preserving normal form may gate the
    hard drop."""
    a, b = "喜欢猫，不喜欢狗", "喜欢狗，不喜欢猫"
    assert token_overlap(fts_tokens(a), fts_tokens(b)) == 1.0
    assert normalized_identity(a) != normalized_identity(b)


def test_identity_ignores_script_case_punctuation_and_stop_names():
    assert normalized_identity("用戶最近養了一隻貓") == normalized_identity(
        "用户最近养了一只猫",
    )
    assert normalized_identity("用户养了猫，很开心") == normalized_identity(
        "用户养了猫。很开心",
    )
    assert normalized_identity("User Likes Cats") == normalized_identity(
        "user likes cats",
    )
    assert normalized_identity("兰兰喜欢猫", ["兰兰"]) == normalized_identity(
        "喜歡貓", ["蘭蘭"],
    )


def test_latin_case_does_not_destroy_the_overlap_score():
    """unicode61 retrieves case-insensitively; scoring must agree or a
    case-only variant scores 0 and looks less alike than unrelated text."""
    assert token_overlap(
        fts_tokens("USER LIKES CATS"), fts_tokens("user likes cats"),
    ) == 1.0


def test_tokenize_refuses_to_fall_back_to_a_different_splitter(monkeypatch):
    """Whatever fts_tokens returns gets persisted. A fallback splitter would
    write rows the normal tokenizer can never match, under a backfill marker
    claiming the index is complete — so it must raise instead."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "memory.hybrid_recall":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    with pytest.raises(ImportError):
        fts_tokens("用户最近养了一只猫")


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
async def test_clause_swap_is_written_and_arbitrated_not_dropped(tmp_path):
    """Codex P1, end of the chain: overlap 1.0 alone must not drop a fact."""
    harness = _harness(tmp_path, [("existing", 1.0)])
    _seed(harness, "喜欢猫，不喜欢狗")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "喜欢狗，不喜欢猫")

    assert len(created) == 1
    resolver.aenqueue_candidates.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_different_entity_is_never_arbitrated(tmp_path):
    """The queue buckets by entity (the vector detector does too); a master
    fact arbitrated against a relationship fact can be merged away."""
    harness = _harness(tmp_path, [("existing", 0.9)])
    _seed(harness, "用户最近养了一只狗", entity="relationship")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=0)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用户最近养了一只猫")

    assert len(created) == 1
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


@pytest.mark.asyncio
async def test_end_to_end_over_a_real_index(tmp_path):
    """Index and query through the real SQLite path.

    Every other Stage-2 test here stubs the index, so none of them would
    notice the two sides disagreeing about what a token is — which is
    exactly the failure #2703 describes.
    """
    cm = _cm(str(tmp_path))
    with patch("memory.timeindex.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm):
        time_index = TimeIndexedMemory(recent_history_manager=MagicMock())
        store = FactStore(time_indexed_memory=time_index)
        store._config_manager = cm
        resolver = MagicMock()
        resolver.aenqueue_candidates = AsyncMock(return_value=1)
        store.attach_dedup_resolver(resolver)

        async def _write(text):
            return await store._apersist_new_facts(
                "小天", [{"text": text, "importance": 7, "entity": "master"}],
                default_source="user_observation", semantic_dedup=True,
            )

        first = await _write("用户最近养了一只猫")
        assert len(first) == 1

        # 繁体重述：Stage-1 的 hash 差得远，靠折叠后的 token 集全同挡住。
        assert await _write("用戶最近養了一隻貓") == []
        resolver.aenqueue_candidates.assert_not_awaited()

        # 一字之差的另一件事：写入 + 进仲裁队列，不能被闸门吃掉。
        dog = await _write("用户最近养了一只狗")
        assert len(dog) == 1
        _, pairs = resolver.aenqueue_candidates.await_args.args
        assert pairs[0]["existing_id"] == first[0]["id"]
        assert pairs[0]["candidate_id"] == dog[0]["id"]

        # 无关的事实：不入队。
        resolver.aenqueue_candidates.reset_mock()
        assert len(await _write("用户喜欢在深夜写代码")) == 1
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


def test_an_unreadable_archive_aborts_the_backfill(tmp_path):
    """Treating a corrupt archive as "no archived rows" would let the
    persistent completion marker land, and those rows would never be
    indexed again — not after repair, not after a restart."""
    import asyncio

    cm = _cm(str(tmp_path))
    with patch("memory.facts.get_config_manager", return_value=cm):
        harness = _PersistHarness(_FakeIndex())
    harness._config_manager = cm

    archive_path = os.path.join(str(tmp_path), "facts_archive.json")
    with open(archive_path, "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json")

    calls: list[int] = []

    class _CountingIndex(_FakeIndex):
        def fts_index_needs_backfill(self, _name):
            return True

        async def abackfill_fact_index(self, _name, rows):
            calls.append(len(rows))
            return len(rows)

    harness._time_indexed = _CountingIndex()
    with patch.object(
        harness, "_facts_archive_path", return_value=archive_path,
    ):
        asyncio.run(harness._aensure_fact_index_backfilled(
            "小天", [{"id": "act1", "text": "用户最近养了一只猫"}],
        ))
        assert calls == []  # 回填根本没跑，标记自然落不下

        # 修好归档之后，下一次写入照常回填。
        with open(archive_path, "w", encoding="utf-8") as fh:
            fh.write('[{"id": "arch1", "text": "群规是不剧透"}]')
        asyncio.run(harness._aensure_fact_index_backfilled(
            "小天", [{"id": "act1", "text": "用户最近养了一只猫"}],
        ))
        assert calls == [2]


def test_backfill_drops_duplicate_ids(index):
    """An interrupted archive commit can leave one id in both facts.json and
    facts_archive.json; the FTS table has no uniqueness constraint, so two
    rows would each eat a candidate slot."""
    from sqlalchemy import text as sql_text

    indexed = index.backfill_fact_index("小天", [
        ("f1", "用户最近养了一只猫"),
        ("f1", "用户最近养了一只猫"),
        ("f2", "用户喜欢喝咖啡"),
    ])
    assert indexed == 2
    with index.engines["小天"].connect() as conn:
        rows = conn.execute(sql_text(
            "SELECT count(*) FROM facts_fts_v2 WHERE fact_id = 'f1'"
        )).fetchone()
    assert rows[0] == 1


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
