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
  4. Stage-2 policy: only a *lossless* normal form (case + the
     tokenizer's separators — no script fold, no diacritic strip, no
     stop-name removal) drops a fact outright. Everything else is
     written AND handed to the LLM arbitration queue.
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


def test_stop_names_go_through_the_same_normalization_as_content():
    """A configured name is matched literally, so any normalization applied
    to the text must reach the name too or it can never be stripped."""
    assert fts_tokens("José sings", ["José"]) == fts_tokens("sings")
    assert fts_tokens("Jose sings", ["José"]) == fts_tokens("sings")


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


def test_identity_ignores_only_lossless_differences():
    """Whitespace runs are the only thing folded — the sole difference that
    provably cannot tell two facts apart."""
    assert normalized_identity("用户  养猫") == normalized_identity("用户 养猫")
    assert normalized_identity("the user  adopted a cat") == normalized_identity(
        "the user adopted a cat",
    )


def test_identity_keeps_characters_that_change_the_claim():
    """The tokenizer's separator class contains meaningful characters, so it
    cannot be reused for a key that authorizes an irreversible drop."""
    assert normalized_identity("balance is -10 dollars") != normalized_identity(
        "balance is 10 dollars",
    )
    assert normalized_identity("体重 1.5 公斤") != normalized_identity("体重 15 公斤")
    assert normalized_identity("a > b") != normalized_identity("a b")
    # 大小写同样保留：`/Foo` 和 `/foo` 在 Linux 上是两个路径。
    assert normalized_identity("the variable is X") != normalized_identity(
        "the variable is x",
    )
    # 空白折成一个空格而不是删掉——删掉会把这两句并成一条。
    assert normalized_identity("an ice cream") != normalized_identity(
        "a nice cream",
    )


def test_identity_keeps_the_names_that_stop_name_stripping_removes():
    """The stop-name list holds the master's AND the catgirl's names, so
    stripping them merges two facts about two different people into one key.
    Retrieval still strips — the pair reaches overlap 1.0 and is arbitrated."""
    master, catgirl = "主人喜欢猫", "兰兰喜欢猫"
    stop_names = ["主人", "兰兰"]
    assert token_overlap(
        fts_tokens(master, stop_names), fts_tokens(catgirl, stop_names),
    ) == 1.0
    assert normalized_identity(master) != normalized_identity(catgirl)


def test_identity_refuses_the_lossy_script_fold():
    """The fold is many-to-one, so it must not gate the hard drop: the two
    surnames below are different and fold to the same character. Retrieval
    still folds — the pair reaches overlap 1.0 and goes to arbitration
    instead of being dropped."""
    a, b = "鍾先生住在台北", "鐘先生住在台北"
    assert token_overlap(fts_tokens(a), fts_tokens(b)) == 1.0
    assert normalized_identity(a) != normalized_identity(b)

    # 同一句话的繁简两写同理：召回够得着，但不许在有损等价上直接丢。
    trad, simp = "用戶最近養了一隻貓", "用户最近养了一只猫"
    assert token_overlap(fts_tokens(trad), fts_tokens(simp)) == 1.0
    assert normalized_identity(trad) != normalized_identity(simp)


def test_latin_case_does_not_destroy_the_overlap_score():
    """unicode61 retrieves case-insensitively; scoring must agree or a
    case-only variant scores 0 and looks less alike than unrelated text."""
    assert token_overlap(
        fts_tokens("USER LIKES CATS"), fts_tokens("user likes cats"),
    ) == 1.0


def test_latin_diacritics_do_not_destroy_the_overlap_score():
    """Same argument as case: unicode61 strips diacritics when matching."""
    assert token_overlap(
        fts_tokens("José habló portugués"), fts_tokens("Jose hablo portugues"),
    ) == 1.0


def test_hangul_survives_the_diacritic_strip():
    """NFD decomposes Hangul into jamo; without the NFC recomposition Korean
    would tokenize into jamo runs instead of syllables."""
    assert "고양이" in fts_tokens("사용자는 고양이를")


def test_tokenize_refuses_to_fall_back_to_a_different_splitter(monkeypatch):
    """Whatever fts_tokens returns gets persisted. A fallback splitter would
    write rows the normal tokenizer can never match, under a backfill marker
    claiming the index is complete — so it must raise instead."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        # persona 是 _tokenize **自己**那条空白切分兜底的触发条件：只挡
        # hybrid_recall 的话，下游那条兜底照样能把整句中文写进索引。
        if name in ("memory.hybrid_recall", "memory.persona"):
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    with pytest.raises(ImportError):
        fts_tokens("用户最近养了一只猫")


def test_tokenize_fails_closed_when_only_persona_is_missing(monkeypatch):
    """hybrid_recall imports fine but its own persona import fails: _tokenize
    silently returns a whitespace split, which for unspaced Chinese is one
    whole-sentence token — the exact shape #2703 is about, now persisted."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "memory.persona":
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
        # 与真实实现同构：SQL 先按 bm25 截窗（这里用给定顺序当 bm25 序），
        # dice 排序发生在**截窗之后**。
        return sorted(self.hits[:limit], key=lambda h: h[1], reverse=True)

    async def aindex_fact(self, *_a, **_k):
        return None

    def fts_index_needs_backfill(self, _name):
        return False


class _PersistHarness(FactStore):
    def __init__(self, time_indexed):
        super().__init__(time_indexed_memory=time_indexed)
        self._mem: list[dict] = []

    async def aload_facts(self, name):
        return self._mem

    # 签名跟着基类走：基类的内部调用点会传 _fact_lock_held=True，
    # 收窄成位置参数的话，某条走到那里的用例会 TypeError 而不是断言失败。
    async def asave_facts(self, name, *, _fact_lock_held: bool = False):
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
async def test_a_lossless_variant_still_drops_the_new_fact(tmp_path):
    harness = _harness(tmp_path, [("existing", FACT_NEAR_DUP_IDENTICAL_OVERLAP)])
    _seed(harness, "用户 最近养了一只猫")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    # 必须是**变体**而不是同一个字符串：同文的话，归一化整个删掉这条也照样绿。
    created = await _persist(harness, "用户  最近养了一只猫")

    assert created == []
    resolver.aenqueue_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_traditional_rewrite_is_arbitrated_not_dropped(tmp_path):
    """The fold that makes the pair *findable* is lossy, so it may not also
    make the pair droppable."""
    harness = _harness(tmp_path, [("existing", FACT_NEAR_DUP_IDENTICAL_OVERLAP)])
    _seed(harness, "用户最近养了一只猫")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用戶最近養了一隻貓")

    assert len(created) == 1
    resolver.aenqueue_candidates.assert_awaited_once()


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
async def test_below_threshold_hits_do_not_eat_the_candidate_budget(tmp_path):
    """OR retrieval can rank a row sharing one rare token above a genuine
    near-duplicate. If those rows consume the 3-candidate budget, the loop
    stops instead of widening to 200 and the real match at rank 11 is never
    examined."""
    low = [(f"low{i}", FACT_NEAR_DUP_ARBITRATE_OVERLAP - 0.01) for i in range(10)]
    harness = _harness(tmp_path, low + [("existing", 0.87)])
    for fid, _ in low:
        harness._mem.append({
            "id": fid, "text": f"用户{fid}偶尔提到猫", "importance": 7,
            "entity": "master", "hash": fid,
        })
    _seed(harness, "用户最近养了一只狗")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用户最近养了一只猫")

    assert len(created) == 1
    resolver.aenqueue_candidates.assert_awaited_once()
    _, pairs = resolver.aenqueue_candidates.await_args.args
    assert pairs[0]["existing_id"] == "existing"


@pytest.mark.asyncio
async def test_widening_still_happens_after_a_merely_eligible_hit(tmp_path):
    """Dice is computed after the SQL LIMIT, so the first window's best by
    bm25 is not its best by overlap. Stopping at a 0.26 hit inside the first
    window leaves the 0.87 duplicate just outside it unarbitrated forever."""
    weak = [("weak", 0.26)] + [(f"pad{i}", 0.0) for i in range(9)]
    harness = _harness(tmp_path, weak + [("existing", 0.87)])
    harness._mem.append({
        "id": "weak", "text": "用户偶尔提到猫", "importance": 7,
        "entity": "master", "hash": "weak",
    })
    _seed(harness, "用户最近养了一只狗")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用户最近养了一只猫")

    assert len(created) == 1
    _, pairs = resolver.aenqueue_candidates.await_args.args
    assert pairs[0]["existing_id"] == "existing"


@pytest.mark.asyncio
async def test_other_entities_do_not_eat_the_candidate_budget(tmp_path):
    """Legacy facts all share one subject boundary, so three higher-ranked
    hits of another entity would exhaust the 3-candidate budget and hide the
    same-entity near-duplicate ranked right behind them."""
    harness = _harness(tmp_path, [
        ("neko1", 0.9), ("neko2", 0.9), ("neko3", 0.9), ("existing", 0.87),
    ])
    for fid in ("neko1", "neko2", "neko3"):
        harness._mem.append({
            "id": fid, "text": f"兰兰{fid}也养了一只狗", "importance": 7,
            "entity": "neko", "hash": fid,
        })
    _seed(harness, "用户最近养了一只狗")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用户最近养了一只猫")

    assert len(created) == 1
    resolver.aenqueue_candidates.assert_awaited_once()
    _, pairs = resolver.aenqueue_candidates.await_args.args
    assert pairs[0]["existing_id"] == "existing"


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
async def test_pairs_are_queued_outside_the_persistence_lock(tmp_path):
    """Lock-order deadlock: enqueueing takes the resolver's per-character
    lock, while ``aresolve`` holds that lock and calls back into
    ``aarchive_arbitrated_facts``, which takes this persistence lock. Both
    sides then wait forever and the character stops accepting facts."""
    harness = _harness(tmp_path, [("existing", 0.87)])
    _seed(harness, "用户最近养了一只猫")
    held: list[bool] = []

    async def _record(name, _pairs):
        held.append(harness._get_persist_alock(name).locked())
        return 1

    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(side_effect=_record)
    harness.attach_dedup_resolver(resolver)

    await _persist(harness, "用户最近养了一只狗")

    assert held == [False]


@pytest.mark.asyncio
async def test_a_dropped_enqueue_leaves_a_trace(tmp_path):
    """aenqueue_candidates returns 0 both for "already queued" and for
    "maintenance mode, the queue file was never written". The caller can't
    tell them apart, but it must not report the second as success.

    Patches the logger rather than using caplog: this repo's module loggers
    don't propagate to root, so caplog sees nothing."""
    harness = _harness(tmp_path, [("existing", 0.87)])
    _seed(harness, "用户最近养了一只猫")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=0)
    harness.attach_dedup_resolver(resolver)

    from memory.facts import logger as facts_logger

    with patch.object(facts_logger, "debug") as debug:
        await _persist(harness, "用户最近养了一只狗")

    assert any("未入队" in str(call.args[0]) for call in debug.call_args_list)


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

        # 只差一个空格：Stage-1 的 hash 挡不住，归一后逐字相同挡住。
        spaced = await _write("the user adopted a cat")
        assert len(spaced) == 1
        resolver.aenqueue_candidates.reset_mock()
        assert await _write("the user  adopted a cat") == []
        resolver.aenqueue_candidates.assert_not_awaited()

        # 繁体重述：召回够得着（折叠），但折叠有损，不许据此直接丢——进仲裁。
        trad = await _write("用戶最近養了一隻貓")
        assert len(trad) == 1
        resolver.aenqueue_candidates.assert_awaited_once()
        resolver.aenqueue_candidates.reset_mock()

        # 一字之差的另一件事：写入 + 进仲裁队列，不能被闸门吃掉。
        dog = await _write("用户最近养了一只狗")
        assert len(dog) == 1
        _, pairs = resolver.aenqueue_candidates.await_args.args
        # first 与 trad 折叠后 token 串逐字相同、bm25 并列，SQLite 的行序未
        # 定义，代码也没承诺挑哪一条——断言只该管「配到了猫那两条之一」。
        assert pairs[0]["existing_id"] in {first[0]["id"], trad[0]["id"]}
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
        json.dump([
            {"id": "arch1", "text": "群规是不剧透"},
            # 仲裁败者：Stage-2 检索到一律跳过，而 restore 明确把它们排除在
            # 外（只留在归档里），所以它们永远不会再变成活跃行——索引里留着
            # 纯占候选窗口。
            {
                "id": "loser1", "text": "群规是不能剧透",
                "arbitration_archived_at": "2026-07-01T00:00:00",
            },
        ], fh)

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
            "小天", [
                {"id": "act1", "text": "用户最近养了一只猫"},
                {"id": 7, "text": "旧的整数 id 行"},
            ],
        ))

    assert captured and dict(captured[0]) == {
        "act1": "用户最近养了一只猫", 7: "旧的整数 id 行", "arch1": "群规是不剧透",
    }
    # id 原样带走：str() 强转会让隐私擦除按原 id 删不掉这一行。
    assert [type(fid) for fid, _ in captured[0]] == [str, int, str]
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


def test_an_unreadable_marker_asks_for_a_backfill(index):
    """Reporting "no backfill needed" when the marker can't be read would let
    the caller record this character as done for the rest of the process —
    history stays out of the index while Stage-2 looks like it works."""
    from sqlalchemy import text as sql_text

    index.backfill_fact_index("小天", [("f1", "用户最近养了一只猫")])
    assert index.fts_index_needs_backfill("小天") is False

    with index.engines["小天"].connect() as conn:
        conn.execute(sql_text("DROP TABLE facts_fts_meta"))
        conn.commit()
    assert index.fts_index_needs_backfill("小天") is True

    with patch.object(
        index, "_ensure_engine_exists", side_effect=RuntimeError("db locked"),
    ):
        assert index.fts_index_needs_backfill("小天") is True


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


def test_backfill_keeps_the_id_type_the_rest_of_the_store_uses(index):
    """This repo deliberately distinguishes a fact id of 1 from "1" (see
    _speaker_trust_fact_id), and FTS5's `WHERE fact_id = :fid` is
    type-sensitive: stringifying on the way in means privacy erasure binding
    the original integer never matches, and the row survives the delete."""
    assert index.backfill_fact_index("小天", [
        (1, "用户最近养了一只猫"),
        ("1", "用户喜欢喝咖啡"),
    ]) == 2

    index.delete_fact_from_index("小天", 1)

    from sqlalchemy import text as sql_text
    with index.engines["小天"].connect() as conn:
        rows = conn.execute(sql_text(
            "SELECT fact_id, typeof(fact_id) FROM facts_fts_v2"
        )).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("1", "text")]


def test_backfill_survives_a_malformed_id(index):
    """facts.json is a plain file users and older versions edited: an id can
    arrive as a list. One such row must not abort the whole backfill — the
    marker would never land and every later write would retry the same scan."""
    assert index.backfill_fact_index("小天", [
        (["not", "an", "id"], "畸形行"),
        ("f1", "用户最近养了一只猫"),
    ]) == 1
    assert index.fts_index_needs_backfill("小天") is False
    assert dict(index.search_similar_facts("小天", "用户前几天养了只猫"))


def test_malformed_ids_are_filtered_before_the_backfill(tmp_path):
    """The same guard on the FactStore side, using the store's own
    _readable_fact_id notion of an unusable id."""
    import asyncio

    cm = _cm(str(tmp_path))
    with patch("memory.facts.get_config_manager", return_value=cm):
        harness = _PersistHarness(_FakeIndex())
    harness._config_manager = cm

    captured: list[list] = []

    class _CapturingIndex(_FakeIndex):
        def fts_index_needs_backfill(self, _name):
            return True

        async def abackfill_fact_index(self, _name, rows):
            captured.append(rows)
            return len(rows)

    harness._time_indexed = _CapturingIndex()
    with patch.object(harness, "_facts_archive_path", return_value=""):
        asyncio.run(harness._aensure_fact_index_backfilled("小天", [
            {"id": ["bad"], "text": "畸形行"},
            {"id": "act1", "text": "用户最近养了一只猫"},
        ]))

    assert captured == [[("act1", "用户最近养了一只猫")]]


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


# ── 6. 仲裁侧的两条契约 ──────────────────────────────────────────


def test_the_arbitration_prompt_never_claims_a_detector(): 
    """The queue is fed by two detectors now (cosine and Dice overlap), and
    an FTS pair can exist with vectors switched off entirely. A prompt that
    tells the model everything came from cosine mislabels the evidence it is
    about to make an irreversible merge/replace call on."""
    from config.prompts.prompts_memory import FACT_DEDUP_PROMPT

    forbidden = (
        "cosine", "向量相似度", "ベクトル類似度", "벡터 유사도",
        "косинус", "similitud vectorial", "similaridade vetorial",
    )
    offenders = {
        locale: word
        for locale, text in FACT_DEDUP_PROMPT.items()
        for word in forbidden
        if word.lower() in text.lower()
    }
    assert offenders == {}


@pytest.mark.asyncio
async def test_restoring_an_arbitration_loser_reopens_the_backfill(tmp_path):
    """Arbitration losers are left out of the backfill, and its marker is
    persistent — so a restore has to reopen it or the row stays invisible to
    Stage-2 forever.

    The restore does NOT index the row itself: ``index_fact`` swallows its
    own errors and returns nothing, so a "failed? then invalidate" branch
    around it could never fire. Invalidating unconditionally is the version
    that cannot silently do nothing.
    """
    import json

    cm = _cm(str(tmp_path))
    cleared: list[str] = []
    backfilled: list[list] = []

    class _MarkerIndex(_FakeIndex):
        def __init__(self):
            super().__init__()
            self.needs = False

        def clear_fts_backfill_marker(self, name):
            cleared.append(name)
            self.needs = True

        def fts_index_needs_backfill(self, _name):
            return self.needs

        async def abackfill_fact_index(self, _name, rows):
            backfilled.append(rows)
            return len(rows)

    index = _MarkerIndex()
    with patch("memory.facts.get_config_manager", return_value=cm):
        store = FactStore(time_indexed_memory=index)
    store._config_manager = cm
    store._fts_backfilled = {"小天"}

    char_dir = os.path.join(str(tmp_path), "小天")
    os.makedirs(char_dir, exist_ok=True)
    with open(os.path.join(char_dir, "facts.json"), "w", encoding="utf-8") as fh:
        json.dump([], fh)
    with open(
        os.path.join(char_dir, "facts_archive.json"), "w", encoding="utf-8",
    ) as fh:
        json.dump([{
            "id": 1, "text": "旧的整数 id 行", "entity": "master",
            "arbitration_archived_at": "2026-07-01T00:00:00",
        }], fh)

    assert await store.arestore_arbitrated_fact("小天", 1) is True
    assert cleared == ["小天"]
    assert "小天" not in store._fts_backfilled

    # 下一次写入的回填必须把这条复活的行带上，且 id 保持原类型。
    await store._aensure_fact_index_backfilled(
        "小天", await store.aload_facts("小天"),
    )
    assert backfilled and (1, "旧的整数 id 行") in backfilled[0]
