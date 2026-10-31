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
  4. Stage-2 policy: only a *byte-identical* text drops a fact
     outright — every normalization tried on this key turned out to
     have a counterexample. Everything else is written AND handed to
     the LLM arbitration queue.
  5. Backfill: facts written before the index was rebuilt get indexed
     once, and the marker stops it from rescanning on every write.
"""
from __future__ import annotations

import os
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.facts import FactStore
from memory.timeindex import (
    FACT_NEAR_DUP_ARBITRATE_OVERLAP,
    TimeIndexedMemory,
    fts_tokens,
    token_overlap,
)
from memory.script_fold import fold_script


# ── 1. tokenization ──────────────────────────────────────────────────


def test_chinese_fact_is_not_one_token():
    """#2703 root cause: unicode61 indexes a run of CJK as a single token."""
    tokens = fts_tokens("用户最近养了一只猫")
    assert len(tokens) > 1
    # Sliding 2-gram + 3-gram windows: nine characters -> eight + seven tokens.
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
    # The paraphrase that really should merge still reaches the arbitration threshold.
    assert token_overlap(rephrase, same) >= FACT_NEAR_DUP_ARBITRATE_OVERLAP







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
        # persona triggers _tokenize's own whitespace fallback: blocking hybrid_recall alone
        # still lets that downstream fallback index entire Chinese sentences.
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
    from pathlib import Path
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.app_docs_dir = Path(tmpdir)
    cm.local_state_dir = Path(tmpdir) / 'local_state'
    cm.local_state_dir.mkdir(exist_ok=True)
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
    assert hits["f1"] == 1.0


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
    # Privacy erasure recognizes only the new table; retaining the old one leaves erased raw text elsewhere.
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

    # Reruns do not duplicate inserts; crashing between insert and marking costs only one rescan.
    assert index.backfill_fact_index(
        "小天", [("f1", "用户最近养了一只猫")],
    ) == 0


# ── 4. Stage-2 policy ────────────────────────────────────────────────


class _FakeIndex:
    def __init__(self, hits=()):
        self.hits = list(hits)

    async def asearch_similar_facts(self, _name, _text, limit):
        # Match real behavior: SQL truncates by bm25 first (given order stands for bm25 here),
        # then Dice sorting occurs after truncation.
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

    # Match the base signature: internal calls pass _fact_lock_held=True;
    # narrowing to positional arguments causes TypeError rather than assertion failure on affected cases.
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
async def test_an_identical_text_still_drops_the_new_fact(tmp_path):
    """The one case left for the hard drop: same text as a row Stage-1's hash
    set doesn't cover (an archived one). Anything else goes to arbitration."""
    harness = _harness(tmp_path, [("existing", 1.0)])
    _seed(harness, "用户最近养了一只猫")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用户最近养了一只猫")

    assert created == []
    resolver.aenqueue_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_whitespace_variant_is_arbitrated_not_dropped(tmp_path):
    """Even collapsing whitespace runs is lossy — `echo 'a  b'` and
    `echo 'a b'` are different commands. The hard drop takes byte equality
    and nothing looser."""
    harness = _harness(tmp_path, [("existing", 1.0)])
    _seed(harness, "用户说 echo 'a  b' 是他常用的命令")
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    harness.attach_dedup_resolver(resolver)

    created = await _persist(harness, "用户说 echo 'a b' 是他常用的命令")

    assert len(created) == 1
    resolver.aenqueue_candidates.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_traditional_rewrite_is_arbitrated_not_dropped(tmp_path):
    """The fold that makes the pair *findable* is lossy, so it may not also
    make the pair droppable."""
    harness = _harness(tmp_path, [("existing", 1.0)])
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
async def test_widening_survives_a_full_budget_of_weak_hits(tmp_path):
    """The 3-candidate budget bounds one pass; it must not double as "no need
    to widen". Three 0.26 hits in the first window would otherwise hide a 0.9
    match at rank 11."""
    weak = [(f"weak{i}", 0.26) for i in range(3)]
    pad = [(f"pad{i}", 0.0) for i in range(7)]
    harness = _harness(tmp_path, weak + pad + [("existing", 0.9)])
    for fid, _ in weak:
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
    _, pairs = resolver.aenqueue_candidates.await_args.args
    assert pairs[0]["existing_id"] == "existing"


@pytest.mark.asyncio
async def test_a_busy_arbitration_queue_does_not_stall_the_write(tmp_path):
    """The resolver holds its per-character lock across a 60s LLM call, and
    callers await _apersist_new_facts directly — the fact is already
    committed, so the request must not wait on unrelated background work."""
    import asyncio as _asyncio

    harness = _harness(tmp_path, [("existing", 0.87)])
    _seed(harness, "用户最近养了一只猫")

    started: list = []

    async def _never_returns(*_a, **_k):
        started.append(_asyncio.current_task())
        await _asyncio.sleep(3600)

    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(side_effect=_never_returns)
    harness.attach_dedup_resolver(resolver)

    with patch("memory.facts.FACT_NEAR_DUP_ENQUEUE_TIMEOUT_SECONDS", 0.01):
        created = await _asyncio.wait_for(
            _persist(harness, "用户最近养了一只狗"), timeout=5,
        )

    assert len(created) == 1
    # Stop waiting without cancelling: cancellation releases the queue lock while the atomic disk write still runs,
    # allowing another writer's read-modify-write to overwrite it.
    assert not started[0].cancelled()
    started[0].cancel()


@pytest.mark.asyncio
async def test_a_background_enqueue_failure_is_logged(tmp_path):
    """Nothing awaits the shielded task, so its exception has to be surfaced
    by the done-callback or it disappears."""
    import asyncio as _asyncio

    from memory.facts import logger as facts_logger

    harness = _harness(tmp_path, [("existing", 0.87)])
    _seed(harness, "用户最近养了一只猫")

    async def _slow_boom(*_a, **_k):
        await _asyncio.sleep(0.05)
        raise RuntimeError("queue unwritable")

    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(side_effect=_slow_boom)
    harness.attach_dedup_resolver(resolver)

    with patch("memory.facts.FACT_NEAR_DUP_ENQUEUE_TIMEOUT_SECONDS", 0.01),             patch.object(facts_logger, "warning") as warning:
        await _persist(harness, "用户最近养了一只狗")
        await _asyncio.sleep(0.1)

    assert any(
        "后台投递" in str(call.args[0]) for call in warning.call_args_list
    )


def test_a_missing_index_table_reopens_the_backfill(index):
    """A marker without the table it describes is a lie: trusting it means the
    history is never rebuilt, and the next index_fact just makes an empty
    table plus the row being written."""
    from sqlalchemy import text as sql_text

    index.backfill_fact_index("小天", [("f1", "用户最近养了一只猫")])
    assert index.fts_index_needs_backfill("小天") is False

    with index.engines["小天"].connect() as conn:
        conn.execute(sql_text("DROP TABLE facts_fts_v2"))
        conn.commit()

    assert index.fts_index_needs_backfill("小天") is True


def test_tokens_are_cut_where_sqlite_would_cut_them():
    """Dice is scored over these tokens while retrieval runs over whatever
    unicode61 made of them. _SPLIT_RE keeps `/`, so `foo/bar` used to be one
    token here and two there: the row was retrieved and then scored 0."""
    assert fts_tokens("foo/bar") == ["foo", "bar"]
    assert token_overlap(fts_tokens("foo/bar"), fts_tokens("foo bar")) == 1.0
    # Remove punctuation-only tokens entirely, avoiding empty quoted terms in FTS queries.
    assert fts_tokens("--- ///") == []
    # CJK n-grams are unaffected: each constituent character is alphanumeric.
    assert "了一只" in fts_tokens("用户最近养了一只猫")


def test_a_single_character_fact_still_gets_a_token():
    """_tokenize starts CJK at 2-grams, so a one-character residue would store
    an empty row and skip Stage-2 entirely."""
    assert fts_tokens("猫") == ["猫"]
    assert token_overlap(fts_tokens("猫"), fts_tokens("貓")) == 1.0
    # Strip stop names from fallback too, or names become unchanged tokens.
    assert fts_tokens("兰兰猫", ["兰兰"]) == fts_tokens("猫")
    assert fts_tokens("") == []


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
    # Text overlap must not impersonate cosine similarity: the value enters the arbitration prompt unchanged.
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

    async def _record(name, _pairs, *, on_persisted=None):
        held.append(harness._get_persist_alock(name).locked())
        if on_persisted is not None:
            await on_persisted()
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
        async def enqueue(_name, _pairs, *, on_persisted=None):
            if on_persisted is not None:
                await on_persisted()
            return 1
        resolver.aenqueue_candidates = AsyncMock(side_effect=enqueue)
        store.attach_dedup_resolver(resolver)

        async def _write(text):
            return await store._apersist_new_facts(
                "小天", [{"text": text, "importance": 7, "entity": "master"}],
                default_source="user_observation", semantic_dedup=True,
            )

        first = await _write("用户最近养了一只猫")
        assert len(first) == 1


        # Traditional paraphrase: folding enables recall but is lossy, so arbitrate rather than dropping directly.
        trad = await _write("用戶最近養了一隻貓")
        assert len(trad) == 1
        resolver.aenqueue_candidates.assert_awaited_once()
        resolver.aenqueue_candidates.reset_mock()

        # A different fact one character away: write and queue for arbitration; the gate must not swallow it.
        dog = await _write("用户最近养了一只狗")
        assert len(dog) == 1
        _, pairs = resolver.aenqueue_candidates.await_args.args
        # first and trad fold into identical token strings and tie on bm25. SQLite row order is unspecified,
        # and the code promises neither choice; assert only a match to one of the two cat-related facts.
        assert pairs[0]["existing_id"] in {first[0]["id"], trad[0]["id"]}
        assert pairs[0]["candidate_id"] == dog[0]["id"]

        # Unrelated fact: do not enqueue.
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
        ], fh)

    captured: list[list[tuple[str, str]]] = []

    class _CapturingIndex(_FakeIndex):
        needs = True

        def fts_index_needs_backfill(self, _name):
            return self.needs

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
    # Preserve IDs unchanged: str() coercion prevents privacy erasure deleting this row by its original ID.
    assert [type(fid) for fid, _ in captured[0]] == [str, int, str]
    # Once persisted, the marker prevents rescans without relying on an in-process cache.
    index_obj = harness._time_indexed
    index_obj.needs = False
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
        assert calls == []  # Backfill never ran, so no marker can be written.

        # After archive repair, the next write backfills normally.
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
    """A failed backfill must not leave a marker behind — the next write has
    to try again. (There is no process-local "done" cache either: it would
    hide a table dropped underneath a running process.)"""
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
        # Retry after failure: no marker was persisted and no in-process cache masks it.
        asyncio.run(harness._aensure_fact_index_backfilled("小天", rows))
        assert attempts == [1, 1]


# -- 6. Two arbitration-side contracts. --


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
async def test_restoring_an_arbitration_loser_needs_no_index_work(tmp_path):
    """Archived rows — losers included — are all in the index, so a restore
    has nothing to schedule.

    The alternative (exclude losers, then re-index or invalidate on restore)
    was tried and reverted: every step of that compensation swallows its own
    errors, so "it failed" and "it worked" are indistinguishable. Keeping the
    rows indexed costs a few candidate-window slots and removes the whole
    failure class.
    """
    import json

    cm = _cm(str(tmp_path))
    touched: list = []

    class _WatchfulIndex(_FakeIndex):
        async def aindex_fact(self, *a, **k):
            touched.append(("index", a))

        def fts_index_needs_backfill(self, _name):
            return False

    with patch("memory.facts.get_config_manager", return_value=cm):
        store = FactStore(time_indexed_memory=_WatchfulIndex())
    store._config_manager = cm

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
    assert touched == []


def test_the_backfill_keeps_arbitration_losers_indexed(tmp_path):
    """Reverted #2703 round 7: excluding them saved a little window space and
    bought a chain of silently-failing compensations on the restore path."""
    import asyncio
    import json

    cm = _cm(str(tmp_path))
    with patch("memory.facts.get_config_manager", return_value=cm):
        harness = _PersistHarness(_FakeIndex())
    harness._config_manager = cm

    archive_path = os.path.join(str(tmp_path), "facts_archive.json")
    with open(archive_path, "w", encoding="utf-8") as fh:
        json.dump([{
            "id": "loser1", "text": "群规是不能剧透",
            "arbitration_archived_at": "2026-07-01T00:00:00",
        }], fh)

    captured: list[list] = []

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
        asyncio.run(harness._aensure_fact_index_backfilled("小天", []))

    assert captured == [[("loser1", "群规是不能剧透")]]


@pytest.mark.asyncio
async def test_portable_retirement_blocks_history_replay_before_signal_scoring(tmp_path):
    import json
    h = _harness(tmp_path, [])
    directory = tmp_path / '小天'
    directory.mkdir(exist_ok=True)
    (directory / 'long_term_portable.json').write_text(json.dumps({'evidence': {'retirements': [
        {'id': 'retired', 'subject': 'master', 'text': '用户喜欢红茶', 'sourceMessageIds': ['sync:' + 'a'*32], 'retiredThroughAt': 2000}
    ], 'confirmations': []}}))
    from utils.llm_client import HumanMessage
    original = HumanMessage(content='我喜欢红茶', additional_kwargs={'memory_source_id': 'sync:'+'a'*32, 'memory_source_created_at': 1000})
    rows = await h._apersist_new_facts('小天', [{'entity': 'master', 'text': '用户喜欢红茶', 'source_indices': [1]}], source_messages=[original])
    assert rows == []
    assert h._mem == []


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['new_confirmation', 'independent_fact', 'old_expanded_sources', 'rollback', 'missing_sources'])
async def test_portable_retirement_uses_trusted_sources_and_preserves_new_evidence(tmp_path, mode):
    import json
    from utils.llm_client import HumanMessage
    from memory.facts import FactExtractionFailed
    h = _harness(tmp_path, [])
    directory = tmp_path / '小天'
    directory.mkdir(exist_ok=True)
    retirement = {'id': 'retired', 'subject': 'master', 'text': '用户喜欢红茶',
                  'sourceMessageIds': ['sync:' + 'a'*32], 'retiredThroughAt': 2000}
    if mode == 'rollback':
        retirement['retiredThroughAt'] = 9_000_000_000_000
    (directory / 'long_term_portable.json').write_text(json.dumps({'evidence': {'retirements': [retirement], 'confirmations': []}}))
    h._allm_call_with_retries = AsyncMock(return_value=[{'index': 0, 'action': 'keep_both'}])
    at = 3000 if mode == 'new_confirmation' else 1000
    sources = [HumanMessage(content='original', additional_kwargs={'memory_source_id': 'sync:'+'b'*32, 'memory_source_created_at': at})]
    text = '用户喜欢游泳' if mode == 'independent_fact' else '用户喜欢红茶'
    fact = {'text': text, 'entity': 'master', 'source_indices': [1], 'source_created_at': '2999-01-01', 'source_message_ids': ['forged']}
    if mode == 'missing_sources':
        fact.pop('source_indices')
        with pytest.raises(FactExtractionFailed):
            await h._apersist_new_facts('小天', [fact], source_messages=sources)
        assert not h._mem
        return
    rows = await h._apersist_new_facts('小天', [fact], source_messages=sources)
    if mode == 'old_expanded_sources':
        assert not rows and not h._mem
    else:
        assert rows[0]['source_message_ids'] == ['sync:'+'b'*32]
        assert rows[0]['source_created_at'] != '2999-01-01'
        assert rows[0]['signal_processed'] is False


@pytest.mark.asyncio
async def test_retired_paraphrases_are_verified_before_persistence_without_truncating_candidates(tmp_path):
    import json
    from utils.llm_client import HumanMessage
    h = _harness(tmp_path, [])
    directory = tmp_path / '小天'
    directory.mkdir(exist_ok=True)
    retirements = [{'id': f'retired-{i}', 'subject': 'master', 'text': f'旧独立事实 {i}',
                    'sourceMessageIds': ['sync:'+'a'*32]} for i in range(25)]
    (directory / 'long_term_portable.json').write_text(json.dumps({'evidence': {'retirements': retirements, 'confirmations': []}}))
    calls = []
    async def adjudicate(prompt, *args, **kwargs):
        calls.append(prompt)
        import re
        indices = [int(value) for value in re.findall(r'^\[(\d+)\] candidate:', prompt, re.M)]
        return [{'index': i, 'action': 'merge' if '旧独立事实 24' in prompt and i == indices[-1] else 'keep_both'} for i in indices]
    h._allm_call_with_retries = adjudicate
    sources = [HumanMessage(content='same raw evidence', additional_kwargs={'memory_source_id': 'sync:'+'a'*32, 'memory_source_created_at': 1000})]
    rows = await h._apersist_new_facts('小天', [{'entity': 'master', 'text': '旧独立事实二十四的另一种措辞', 'source_indices': [1]}], source_messages=sources)
    assert not rows and not h._mem
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_retired_assertion_review_failure_does_not_write_half_a_batch(tmp_path):
    import json
    from utils.llm_client import HumanMessage
    from memory.facts import FactExtractionFailed
    h = _harness(tmp_path, [])
    directory = tmp_path / '小天'
    directory.mkdir(exist_ok=True)
    (directory / 'long_term_portable.json').write_text(json.dumps({'evidence': {'retirements': [
        {'id': 'old', 'subject': 'master', 'text': '喜欢红茶', 'sourceMessageIds': ['raw']}
    ], 'confirmations': []}}))
    h._allm_call_with_retries = AsyncMock(return_value=[])
    sources = [HumanMessage(content='raw', additional_kwargs={'memory_source_id': 'raw', 'memory_source_created_at': 1000})]
    with pytest.raises(FactExtractionFailed):
        await h._apersist_new_facts('小天', [{'entity': 'master', 'text': 'a real independent fact', 'source_indices': [1]}], source_messages=sources)
    assert not h._mem


@pytest.mark.asyncio
async def test_cancelling_retired_assertion_review_leaves_no_fact_or_signal_for_retry(tmp_path):
    import asyncio
    import json
    from utils.llm_client import HumanMessage
    h = _harness(tmp_path, [])
    directory = tmp_path / '小天'
    directory.mkdir(exist_ok=True)
    (directory / 'long_term_portable.json').write_text(json.dumps({'evidence': {'retirements': [
        {'id': 'old', 'subject': 'master', 'text': 'old assertion', 'sourceMessageIds': ['raw']}
    ], 'confirmations': []}}))
    reached = asyncio.Event()
    async def blocked(*args, **kwargs):
        reached.set()
        await asyncio.Event().wait()
    h._allm_call_with_retries = blocked
    source = HumanMessage(content='raw', additional_kwargs={'memory_source_id': 'raw', 'memory_source_created_at': 1000})
    candidate = {'entity': 'master', 'text': 'an independent assertion', 'source_indices': [1]}
    task = asyncio.create_task(h._apersist_new_facts('小天', [candidate], source_messages=[source]))
    await asyncio.wait_for(reached.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not h._mem
    h._allm_call_with_retries = AsyncMock(return_value=[{'index': 0, 'action': 'keep_both'}])
    assert len(await h._apersist_new_facts('小天', [candidate], source_messages=[source])) == 1
    h._allm_call_with_retries = AsyncMock()
    assert await h._apersist_new_facts('小天', [], source_messages=[source]) == []
    h._allm_call_with_retries.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('at,expected', [(None, 0), ('1000', 0), ('3000', 1)])
async def test_legacy_private_extractor_uses_original_message_metadata_for_retirement(tmp_path, at, expected):
    import json
    from utils.llm_client import HumanMessage
    h = _harness(tmp_path, [])
    directory = tmp_path / '小天'
    directory.mkdir(exist_ok=True)
    old_id = 'a'*32
    source_id = old_id if at is None else 'b'*32
    (directory / 'long_term_portable.json').write_text(json.dumps({'evidence': {'retirements': [
        {'id': 'old', 'subject': 'master', 'text': '用户喜欢红茶', 'sourceMessageIds': ['sync:'+old_id], 'retiredThroughAt': 2000}
    ], 'confirmations': []}}))
    h._allm_extract_facts = AsyncMock(return_value=[{'entity': 'master', 'text': '用户喜欢红茶', 'source_indices': [1]}])
    sources = [HumanMessage(content='original source', metadata={'recent_memory_id': source_id, 'recent_memory_time': at})]
    rows = await h.extract_facts(sources, '小天')
    assert len(rows) == expected


def _portable_retirement(tmp_path, *, correction=False):
    directory = tmp_path / '小天'
    directory.mkdir(exist_ok=True)
    row = {'id': 'retired', 'subject': 'master', 'text': '用户喜欢红茶', 'sourceMessageIds': ['sync:' + 'a'*32]}
    row.update({'retiredBySourceMessageIds': ['sync:' + 'c'*32]} if correction else {'retiredThroughAt': 2000})
    state = {'evidence': {'retirements': [row], 'confirmations': []}}
    (directory / 'long_term_portable.json').write_text(json.dumps(state))
    return directory


def _trusted_sync_message(identity, at, text='trusted original', cls=None):
    from utils.llm_client import HumanMessage
    return (cls or HumanMessage)(content=text, additional_kwargs={'memory_source_id': 'sync:'+identity*32, 'memory_source_created_at': at})


@pytest.mark.asyncio
@pytest.mark.parametrize('at', [0, 1234, None])
@pytest.mark.parametrize('entity,source,role', [
    ('master', 'user_observation', 'human'),
    ('neko', 'user_observation', 'human'),
    ('neko', 'ai_disclosure', 'ai'),
])
async def test_fresh_character_records_trusted_fact_sources_without_retirements(tmp_path, entity, source, role, at):
    from utils.llm_client import AIMessage, HumanMessage
    h = _harness(tmp_path, [])
    rows = await h._apersist_new_facts('小天', [
        {'entity': entity, 'source': source, 'text': 'an independent grounded fact', 'source_indices': [1]}],
        source_messages=[_trusted_sync_message('a', at, cls=AIMessage if role == 'ai' else HumanMessage)])
    assert len(rows) == 1
    assert rows[0]['source_message_ids'] == ['sync:' + 'a' * 32]
    from memory.long_term_sync import timestamp
    if at is None:
        assert 'source_created_at' not in rows[0]
    else:
        assert timestamp(rows[0]['source_created_at']) == at
    assert rows[0]['entity'] == entity


def test_fresh_character_extraction_prompt_identifies_speakers_and_sources(tmp_path):
    from memory.long_term_sync import retirement_source_prompt
    from utils.llm_client import AIMessage
    messages = [_trusted_sync_message('a', 1000, 'My notebook has a silver cover.'),
                _trusted_sync_message('b', 2000, 'I remember your notebook.', AIMessage)]
    rendered, instruction = retirement_source_prompt(tmp_path, messages, 'unlabelled',
                                                     {'human': 'User', 'ai': 'Neko'})
    assert '[SOURCE 1]' in rendered and '[SOURCE 2]' in rendered
    assert 'human' in rendered and 'ai' in rendered
    assert 'source_indices' in instruction


@pytest.mark.asyncio
@pytest.mark.parametrize('previous_source,new_time', [
    ('user_observation', 2000), ('ai_disclosure', 2000),
    ('user_observation', 500), ('ai_disclosure', 0),
])
async def test_reconfirmation_updates_sources_without_duplicate_fact_or_signal(tmp_path, previous_source, new_time):
    from utils.llm_client import AIMessage
    from memory.long_term_sync import timestamp
    h = _harness(tmp_path, [])
    candidate = {'entity': 'master', 'source': previous_source, 'text': 'user prefers tea', 'source_indices': [1]}
    await h._apersist_new_facts('Neko', [candidate], source_messages=[
        _trusted_sync_message('a', 1000, cls=AIMessage if previous_source == 'ai_disclosure' else None)])
    h._mem[0]['signal_processed'] = True
    h._mem[0]['absorbed'] = True
    confirmation = {**candidate, 'source': 'user_observation'}
    sources = [_trusted_sync_message('b', new_time)]
    for attempt in range(2):
        reconciled = []
        assert await h._apersist_new_facts('Neko', [confirmation], source_messages=sources, reconciled_facts=reconciled) == []
        assert len(reconciled) == (1 if attempt == 0 else 0)
        assert len(h._mem) == 1
        fact = h._mem[0]
        assert fact['source'] == 'user_observation'
        assert fact['source_message_ids'] == (['sync:' + 'a' * 32, 'sync:' + 'b' * 32]
                                             if previous_source == 'user_observation' else ['sync:' + 'b' * 32])
        assert timestamp(fact['source_created_at']) == (max(1000, new_time) if previous_source == 'user_observation' else new_time)
        assert fact['signal_processed'] is (previous_source == 'user_observation')
        assert fact['absorbed'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [OSError, asyncio.CancelledError])
async def test_reconfirmation_source_update_rolls_back_before_retry(tmp_path, failure):
    from copy import deepcopy
    h = _harness(tmp_path, [])
    candidate = {'entity': 'master', 'text': 'user prefers tea', 'source_indices': [1]}
    await h._apersist_new_facts('Neko', [candidate], source_messages=[_trusted_sync_message('a', 1000)])
    before = deepcopy(h._mem)
    h.asave_facts = AsyncMock(side_effect=failure())
    with pytest.raises(failure):
        await h._apersist_new_facts('Neko', [candidate], source_messages=[_trusted_sync_message('b', 2000)])
    assert h._mem == before
    h.asave_facts = AsyncMock()
    await h._apersist_new_facts('Neko', [candidate], source_messages=[_trusted_sync_message('b', 2000)])
    assert h._mem[0]['source_message_ids'] == ['sync:' + 'a' * 32, 'sync:' + 'b' * 32]


@pytest.mark.asyncio
@pytest.mark.parametrize('old_time,new_time,expected', [(None, 2000, None), (1000, None, None), (0, 0, 0)])
async def test_reconfirmation_preserves_unknown_time_and_epoch_zero(tmp_path, old_time, new_time, expected):
    from memory.long_term_sync import timestamp
    h = _harness(tmp_path, [])
    row = {'entity': 'master', 'text': 'user prefers tea', 'source_indices': [1]}
    await h._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('a', old_time)])
    await h._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('b', new_time)])
    value = h._mem[0].get('source_created_at')
    assert (timestamp(value) if value is not None else None) == expected


@pytest.mark.asyncio
async def test_reconfirmation_sources_survive_real_save_and_lower_trust_replay(tmp_path):
    from memory.long_term_sync import timestamp
    from utils.llm_client import AIMessage
    with patch('memory.facts.get_config_manager', return_value=_cm(str(tmp_path))):
        store = FactStore()
    row = {'entity': 'master', 'text': 'user prefers tea', 'source_indices': [1]}
    await store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('a', 1000)])
    await store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('b', 2000)])
    await store._apersist_new_facts('Neko', [{**row, 'source': 'ai_disclosure'}],
        source_messages=[_trusted_sync_message('c', 3000, cls=AIMessage)])
    store._facts.clear()
    fact = (await store.aload_facts('Neko'))[0]
    assert fact['source_message_ids'] == ['sync:' + 'a' * 32, 'sync:' + 'b' * 32]
    assert timestamp(fact['source_created_at']) == 2000
    assert fact['source'] == 'user_observation'


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_source', [None, 'chat'])
async def test_reconfirmation_keeps_legacy_user_sources_free_of_ai_evidence(tmp_path, legacy_source):
    from copy import deepcopy
    from utils.llm_client import AIMessage
    with patch('memory.facts.get_config_manager', return_value=_cm(str(tmp_path))):
        store = FactStore()
    row = {'entity': 'master', 'text': 'user prefers tea', 'source_indices': [1]}
    await store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('a', 1000)])
    fact = (await store.aload_facts('Neko'))[0]
    if legacy_source is None:
        fact.pop('source')
    else:
        fact['source'] = legacy_source
    await store.asave_facts('Neko')
    before = deepcopy(fact)
    await store._apersist_new_facts('Neko', [{**row, 'source': 'ai_disclosure'}],
        source_messages=[_trusted_sync_message('b', 2000, cls=AIMessage)])
    store._facts.clear()
    assert (await store.aload_facts('Neko'))[0] == before


@pytest.mark.asyncio
async def test_reconfirmation_unknown_source_time_survives_import_and_export(tmp_path):
    from memory.long_term_sync import export_document, import_document, read_views
    with patch('memory.facts.get_config_manager', return_value=_cm(str(tmp_path))):
        store = FactStore()
    row = {'entity': 'master', 'text': 'user prefers tea', 'source_indices': [1]}
    await store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('a', 0)])
    views = read_views(tmp_path / 'Neko')
    store._facts['Neko'] = import_document(export_document(views, 2000), views)['facts']
    await store.asave_facts('Neko')
    await store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('b', None)])
    native = read_views(tmp_path / 'Neko')
    assert 'source_created_at' not in native['facts'][0]
    portable = export_document(native, 3000)
    assert 'sourceCreatedAt' not in portable['entries'][0]['metadata']
    assert 'source_created_at' not in import_document(portable, native)['facts'][0]


@pytest.mark.asyncio
async def test_reconfirmation_retains_all_native_sources_but_projects_recent_200(tmp_path):
    from memory.long_term_sync import export_document, import_document, read_views
    from utils.llm_client import HumanMessage
    with patch('memory.facts.get_config_manager', return_value=_cm(str(tmp_path))):
        store = FactStore()
    sources = [HumanMessage(content='I prefer tea.', additional_kwargs={
        'memory_source_id': 'sync:' + f'{index:032x}', 'memory_source_created_at': 1000 + index,
    }) for index in range(201)]
    row = {'entity': 'master', 'text': 'user prefers tea', 'source_indices': list(range(1, 201))}
    await store._apersist_new_facts('Neko', [row], source_messages=sources[:200])
    await store._apersist_new_facts('Neko', [{**row, 'source_indices': [1]}], source_messages=sources[200:])
    native = read_views(tmp_path / 'Neko')
    full = native['facts'][0]['source_message_ids']
    assert len(full) == 201
    portable = export_document(native, 3000)
    assert portable['entries'][0]['metadata']['sourceMessageIds'] == full[-200:]
    assert 'sourceCreatedAt' not in portable['entries'][0]['metadata']
    roundtrip = import_document(portable, native)
    assert roundtrip['facts'][0]['source_message_ids'] == full
    assert roundtrip['facts'][0]['source_created_at'] == native['facts'][0]['source_created_at']
    assert export_document(roundtrip, 3000) == portable
    assert read_views(tmp_path / 'Neko') == native


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_reconfirmation_rolls_back_when_later_candidate_search_is_interrupted(tmp_path, cancel):
    from copy import deepcopy
    with patch('memory.facts.get_config_manager', return_value=_cm(str(tmp_path))):
        store = FactStore()
    row = {'entity': 'master', 'text': 'user prefers tea', 'source_indices': [1]}
    await store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('a', 1000)])
    before = deepcopy(await store.aload_facts('Neko'))
    entered = asyncio.Event()
    class InterruptedIndex(_FakeIndex):
        async def asearch_similar_facts(self, *_args):
            entered.set()
            if not cancel:
                raise OSError('synthetic_search_failure')
            await asyncio.Event().wait()
    store._time_indexed = InterruptedIndex()
    task = asyncio.create_task(store._apersist_new_facts('Neko', [row, {**row, 'text': 'user likes cats'}],
        source_messages=[_trusted_sync_message('b', 2000)]))
    await asyncio.wait_for(entered.wait(), 3)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else OSError):
        await task
    assert await store.aload_facts('Neko') == before
    await store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('b', 2000)])
    store._facts.clear()
    assert len((await store.aload_facts('Neko'))[0]['source_message_ids']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('write_fails', [False, True])
async def test_cancelled_successful_fact_save_still_enqueues_arbitration(tmp_path, monkeypatch, write_fails):
    import threading
    from memory import facts
    from memory.fact_dedup import FactDedupResolver
    with patch('memory.facts.get_config_manager', return_value=_cm(str(tmp_path))):
        store = FactStore()
    row = {'entity': 'master', 'text': 'user likes cats', 'source_indices': [1]}
    await store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('a', 1000)])
    existing = (await store.aload_facts('Neko'))[0]
    store._time_indexed = _FakeIndex([(existing['id'], .9)])
    resolver = FactDedupResolver(store)
    store.attach_dedup_resolver(resolver)
    entered, release = threading.Event(), threading.Event()
    original = facts.atomic_write_json
    def blocked(path, *args, **kwargs):
        if str(path) == store._facts_path('Neko'):
            entered.set()
            assert release.wait(3)
            if write_fails:
                raise OSError('synthetic_write_failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(facts, 'atomic_write_json', blocked)
    candidate = {**row, 'text': 'user enjoys cats'}
    task = asyncio.create_task(store._apersist_new_facts('Neko', [candidate], source_messages=[_trusted_sync_message('b', 2000)]))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert len(await resolver.aload_pending('Neko')) == (0 if write_fails else 1)
    assert len(await store.aload_facts('Neko')) == (1 if write_fails else 2)
    monkeypatch.setattr(facts, 'atomic_write_json', original)
    await store._apersist_new_facts('Neko', [candidate], source_messages=[_trusted_sync_message('b', 2000)])
    assert len(await resolver.aload_pending('Neko')) == 1
    assert len(await store.aload_facts('Neko')) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('write_fails', [False, True])
async def test_cancel_during_reconfirmation_save_waits_for_consistent_committed_sources(tmp_path, monkeypatch, write_fails):
    import threading
    from memory import facts
    with patch('memory.facts.get_config_manager', return_value=_cm(str(tmp_path))):
        store = FactStore()
    row = {'entity': 'master', 'text': 'user prefers tea', 'source_indices': [1]}
    await store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('a', 1000)])
    reached, release = threading.Event(), threading.Event()
    original = facts.atomic_write_json
    def blocked(path, *args, **kwargs):
        if str(path) == store._facts_path('Neko'):
            reached.set()
            assert release.wait(3)
            if write_fails:
                raise OSError('synthetic_save_failure_after_cancel')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(facts, 'atomic_write_json', blocked)
    task = asyncio.create_task(store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('b', 2000)]))
    try:
        assert await asyncio.to_thread(reached.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    cached = await store.aload_facts('Neko')
    with open(store._facts_path('Neko'), encoding='utf-8') as handle:
        disk = json.load(handle)
    assert cached == disk
    assert cached[0]['source_message_ids'] == ['sync:' + 'a' * 32] + ([] if write_fails else ['sync:' + 'b' * 32])
    monkeypatch.setattr(facts, 'atomic_write_json', original)
    assert await store._apersist_new_facts('Neko', [row], source_messages=[_trusted_sync_message('b', 2000)]) == []
    assert (await store.aload_facts('Neko'))[0]['source_message_ids'] == ['sync:' + 'a' * 32, 'sync:' + 'b' * 32]


@pytest.mark.asyncio
@pytest.mark.parametrize('indices', [None, [], [True], [0], [3], [2]])
async def test_fresh_trusted_batch_rejects_unverified_user_observation_sources(tmp_path, indices):
    from utils.llm_client import AIMessage
    h = _harness(tmp_path, [])
    sources = [_trusted_sync_message('a', 1000, 'My notebook has a silver cover.'),
               _trusted_sync_message('b', 2000, 'I remember your notebook.', AIMessage)]
    rows = await h._apersist_new_facts('小天', [
        {'entity': 'master', 'text': 'a valid independent fact', 'source_indices': [1]},
        {'entity': 'neko', 'text': 'an unsupported candidate', 'source_indices': indices}],
        source_messages=sources)
    assert [row['text'] for row in rows] == ['a valid independent fact']
    assert all(row['text'] != 'an unsupported candidate' for row in h._mem)


@pytest.mark.asyncio
async def test_entity_fallback_cannot_bypass_retired_paraphrase_review(tmp_path):
    _portable_retirement(tmp_path)
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = AsyncMock(return_value=[{'index':0,'action':'merge'}])
    rows = await h._apersist_new_facts('小天', [{'entity':'user','text':'红茶是用户偏好的饮品','source_indices':[1]}], source_messages=[_trusted_sync_message('a',1000)])
    print(json.dumps({'probe':'entity_fallback','review_calls':h._allm_call_with_retries.await_count,'persisted':[(r['entity'],r['signal_processed']) for r in rows]}))
    assert rows == []


@pytest.mark.asyncio
async def test_invalid_source_fallback_cannot_treat_ai_as_user_confirmation(tmp_path):
    from utils.llm_client import AIMessage
    from memory.facts import FactExtractionFailed
    _portable_retirement(tmp_path)
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = AsyncMock()
    with pytest.raises(FactExtractionFailed):
        await h._apersist_new_facts('小天', [{'entity':'master','source':'invalid_model_enum','text':'用户喜欢红茶','source_indices':[2]}],
            source_messages=[_trusted_sync_message('b',3000,'unrelated user line'), _trusted_sync_message('d',4000,'AI repeats old preference', AIMessage)])
    assert not h._mem



@pytest.mark.asyncio
async def test_available_correction_source_time_allows_genuine_new_confirmation(tmp_path):
    _portable_retirement(tmp_path, correction=True)
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = AsyncMock()
    try:
        rows = await h._apersist_new_facts('小天',[{'entity':'master','text':'用户喜欢红茶','source_indices':[2]}],
            source_messages=[_trusted_sync_message('c',2000,'correction source'),_trusted_sync_message('b',3000,'new explicit confirmation')])
    except Exception as e:
        print(json.dumps({'probe':'correction_time','error':str(e),'fact_count':len(h._mem)}))
        raise
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_punctuation_sensitive_independent_fact_is_not_a_retired_assertion(tmp_path):
    directory = _portable_retirement(tmp_path)
    state = json.loads((directory/'long_term_portable.json').read_text())
    state['evidence']['retirements'][0]['text'] = '用户掌握C++'
    (directory/'long_term_portable.json').write_text(json.dumps(state))
    h = _harness(tmp_path,[])
    h._allm_call_with_retries = AsyncMock(return_value=[{'index':0,'action':'keep_both'}])
    rows = await h._apersist_new_facts('小天',[{'entity':'master','text':'用户掌握C','source_indices':[1]}],
        source_messages=[_trusted_sync_message('a',1000,'我掌握C和C++，它们是两门独立语言')])
    print(json.dumps({'probe':'independent_punctuation','persisted_count':len(rows),'review_calls':h._allm_call_with_retries.await_count}))
    assert len(rows) == 1



@pytest.mark.asyncio
async def test_late_correction_time_is_resolved_from_original_store_and_survives_retry(tmp_path):
    import json
    directory = _portable_retirement(tmp_path, correction=True)
    h = _harness(tmp_path, [])
    h._time_indexed.aget_original_source_evidence = AsyncMock(return_value={'sync:'+'c'*32:('human',2000)})
    row = {'entity':'master','text':'用户喜欢红茶','source_indices':[1]}
    assert len(await h._apersist_new_facts('小天', [row], source_messages=[_trusted_sync_message('b',3000)])) == 1
    evidence = json.loads((directory/'long_term_portable.json').read_text())['evidence']['retirements'][0]
    assert evidence['retiredThroughAt'] == 2000
    assert evidence['retiredThroughAtFromCorrection'] is True
    h._time_indexed.aget_original_source_evidence.assert_awaited_once()
    reopened = _harness(tmp_path, [])
    assert await reopened._apersist_new_facts('小天', [row], source_messages=[_trusted_sync_message('d',1500)]) == []


@pytest.mark.asyncio
async def test_retirement_clock_repair_does_not_persist_expanded_display_text(tmp_path):
    from memory.long_term_sync import export_document, read_views
    directory = _portable_retirement(tmp_path, correction=True)
    path = directory / 'long_term_portable.json'
    state = json.loads(path.read_text())
    retirement = state['evidence']['retirements'][0]
    retirement.pop('text')
    text = 'Previous independent preference. ' * 200
    dormant = {'kind': 'fact', 'subject': 'master', 'id': 'retired', 'text': text,
               'metadata': {'createdAt': 1000, 'status': 'retracted', 'voidedAt': 2000}}
    state['dormantEntries'] = [dormant]
    path.write_text(json.dumps(state), encoding='utf-8')
    h = _harness(tmp_path, [])
    h._time_indexed.aget_original_source_evidence = AsyncMock(return_value={'sync:'+'c'*32: ('human', 2000)})
    assert len(await h._apersist_new_facts('小天', [{'entity': 'master', 'text': text, 'source_indices': [1]}],
                                         source_messages=[_trusted_sync_message('b', 3000)])) == 1
    repaired = json.loads(path.read_text())
    assert repaired['evidence']['retirements'] == [
        {**retirement, 'retiredThroughAt': 2000, 'retiredThroughAtFromCorrection': True}]
    assert repaired['dormantEntries'] == [dormant]
    assert export_document(read_views(directory), 4000)['evidence']['retirements'] == repaired['evidence']['retirements']


@pytest.mark.asyncio
async def test_unprovenanced_facts_use_only_exact_retirement_matching(tmp_path):
    _portable_retirement(tmp_path)
    h = _harness(tmp_path, [])
    h._llm_request = AsyncMock(side_effect=AssertionError('unprovenanced facts must not trigger semantic review'))
    texts = ['用户喜欢红茶', 'The user likes black tea', 'The user has a round badge']
    rows = await h._apersist_new_facts('小天', [{'entity': 'master', 'text': text} for text in texts])
    assert [row['text'] for row in rows] == texts[1:]
    h._llm_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_unresolved_correction_is_a_data_wait_and_not_a_terminal_model_failure(tmp_path):
    from memory.facts import FactExtractionFailed
    _portable_retirement(tmp_path, correction=True)
    h = _harness(tmp_path, [])
    for _ in range(6):
        try:
            await h._apersist_new_facts('小天', [{'entity':'master','text':'用户喜欢红茶','source_indices':[1]}], source_messages=[_trusted_sync_message('b',3000)])
        except FactExtractionFailed:
            pytest.fail('waiting for original evidence consumed the terminal model failure budget')
        except RuntimeError as error:
            assert str(error) == 'retired_correction_time_unavailable'
        else:
            pytest.fail('unknown correction time accepted an unverified assertion')
    assert not h._mem


def test_original_source_time_lookup_uses_only_real_user_metadata(index):
    from datetime import datetime
    from utils.llm_client import HumanMessage, AIMessage
    uid = 'c'*32
    index.store_conversation_strict('proof-time', [
        HumanMessage(content='real correction', metadata={'recent_memory_id':uid,'recent_memory_time':'2000'}),
        AIMessage(content='separate assistant original', metadata={'recent_memory_id':'e'*32,'recent_memory_time':'9000'}),
        HumanMessage(content=uid, metadata={'recent_memory_id':'d'*32,'recent_memory_time':'8000'}),
    ], '小天', datetime(2026,10,26))
    assert index.get_original_source_evidence('小天', ['sync:'+uid]) == {'sync:'+uid:('human',2000)}


@pytest.mark.asyncio
async def test_neko_correction_clock_uses_assistant_sources_without_replaying_user_scores(tmp_path):
    from utils.llm_client import AIMessage
    directory = _portable_retirement(tmp_path, correction=True)
    state = json.loads((directory/'long_term_portable.json').read_text())
    state['evidence']['retirements'][0].update(subject='neko', text='猫娘喜欢红茶')
    (directory/'long_term_portable.json').write_text(json.dumps(state))
    h = _harness(tmp_path, [])
    rows = await h._apersist_new_facts('小天', [{'entity':'neko','source':'ai_disclosure','text':'猫娘喜欢红茶','source_indices':[2]}],
        source_messages=[_trusted_sync_message('c',2000,cls=AIMessage),_trusted_sync_message('b',3000,cls=AIMessage)])
    assert len(rows) == 1 and rows[0]['signal_processed'] is True
    assert json.loads((directory/'long_term_portable.json').read_text())['evidence']['retirements'][0]['retiredThroughAt'] == 2000


@pytest.mark.asyncio
async def test_cancel_during_correction_evidence_save_finishes_repair_without_writing_facts(tmp_path, monkeypatch):
    import asyncio
    import threading
    from utils import file_utils
    directory = _portable_retirement(tmp_path, correction=True)
    h = _harness(tmp_path, [])
    reached, release = threading.Event(), threading.Event()
    original = file_utils.atomic_write_json
    def paused(path, *args, **kwargs):
        if path.name == 'long_term_portable.json':
            reached.set()
            assert release.wait(3)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(file_utils, 'atomic_write_json', paused)
    messages = [_trusted_sync_message('c',2000),_trusted_sync_message('b',3000)]
    candidate = {'entity':'master','text':'用户喜欢红茶','source_indices':[2]}
    task = asyncio.create_task(h._apersist_new_facts('小天', [candidate], source_messages=messages))
    try:
        assert await asyncio.to_thread(reached.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not h._mem
    assert json.loads((directory/'long_term_portable.json').read_text())['evidence']['retirements'][0]['retiredThroughAt'] == 2000
    assert len(await h._apersist_new_facts('小天', [candidate], source_messages=messages)) == 1



def test_original_source_lookup_preserves_unknown_time_and_rejects_conflicting_roles(index):
    from datetime import datetime
    from utils.llm_client import HumanMessage, AIMessage
    missing, conflict = 'c'*32, 'd'*32
    index.store_conversation_strict('unknown-time', [HumanMessage(content='unknown original time',
        metadata={'recent_memory_id':missing,'recent_memory_time':None})], '小天', datetime(2026,10,26))
    assert index.get_original_source_evidence('小天', ['sync:'+missing]) == {}
    index.store_conversation_strict('conflicting-identity', [
        HumanMessage(content='first role', metadata={'recent_memory_id':conflict,'recent_memory_time':'2000'}),
        AIMessage(content='conflicting role', metadata={'recent_memory_id':conflict,'recent_memory_time':'3000'}),
    ], '小天', datetime(2026,10,26))
    assert index.get_original_source_evidence('小天', ['sync:'+conflict]) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('empty', [True, False])
async def test_empty_or_independent_batch_does_not_wait_for_unrelated_correction_sources(tmp_path, empty):
    _portable_retirement(tmp_path, correction=True)
    h = _harness(tmp_path, [])
    h._time_indexed.aget_original_source_evidence = AsyncMock(side_effect=OSError('unavailable original evidence'))
    h._allm_call_with_retries = AsyncMock(return_value=[{'index':0,'action':'keep_both'}])
    facts = [] if empty else [{'entity':'master','text':'用户喜欢游泳','source_indices':[1]}]
    result = await h._apersist_new_facts('小天', facts, source_messages=[_trusted_sync_message('a',1000)])
    assert len(result) == (0 if empty else 1)
    h._time_indexed.aget_original_source_evidence.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('during_lookup', [False, True])
async def test_correction_time_repair_obeys_maintenance_at_commit(tmp_path, during_lookup):
    from tests.unit.test_evidence_archive import _install
    from utils.cloudsave_runtime import MaintenanceModeError
    directory = _portable_retirement(tmp_path, correction=True)
    path = directory / 'long_term_portable.json'
    before = path.read_bytes()
    _ev, fs, _pm, _re, _rec, cm = _install(str(tmp_path))
    cm.app_docs_dir, cm.local_state_dir = tmp_path, tmp_path / 'local_state'
    cm.local_state_dir.mkdir()
    state = {'mode': 'normal' if during_lookup else 'maintenance_readonly'}
    cm.load_root_state.side_effect = lambda: state

    async def lookup(*_args):
        state['mode'] = 'maintenance_readonly'
        return {'sync:' + 'c'*32: ('human', 2000)}

    fs._time_indexed = _FakeIndex()
    fs._time_indexed.aget_original_source_evidence = lookup
    messages = [_trusted_sync_message('b', 3000)] if during_lookup else [
        _trusted_sync_message('c', 2000), _trusted_sync_message('b', 3000)]
    candidate = {'entity': 'master', 'text': '用户喜欢红茶', 'source_indices': [len(messages)]}
    with pytest.raises(MaintenanceModeError):
        await fs._apersist_new_facts('小天', [candidate], semantic_dedup=False, source_messages=messages)
    assert path.read_bytes() == before
    assert not (directory / 'facts.json').exists()


@pytest.mark.parametrize('original_time', [
    '2026-10-01T12:00:00+08:00', '2026-10-01T04:00:00Z',
    '2026-09-30T23:00:00.125-05:00',
])
def test_original_source_lookup_recovers_valid_iso_time_without_writing_originals(index, original_time):
    from datetime import datetime
    from pathlib import Path
    from utils.llm_client import HumanMessage
    uid = 'c'*32
    index.store_conversation_strict('iso-correction', [HumanMessage(content='real correction', metadata={
        'chat_original_source': {'recordId': uid, 'originalTime': original_time}})], '小天', datetime(2026, 10, 26))
    path = Path(index.db_paths['小天'])
    before = path.read_bytes()
    expected = int(datetime.fromisoformat(original_time).timestamp() * 1000)
    assert index.get_original_source_evidence('小天', ['sync:' + uid]) == {'sync:' + uid: ('human', expected)}
    assert path.read_bytes() == before


@pytest.mark.parametrize('original_time', [
    None, 'not a timestamp', '2026-02-30T12:00:00Z', '2026-10-01T12:00:00+01:99',
    '2026-10-01T12:00:00', '9007199254740991',
])
def test_original_source_lookup_never_substitutes_save_time_for_invalid_original_time(index, original_time):
    from datetime import datetime
    from utils.llm_client import HumanMessage
    uid = 'c'*32
    index.store_conversation_strict('unknown-original', [HumanMessage(content='original', metadata={
        'chat_original_source': {'recordId': uid, 'originalTime': original_time}})], '小天', datetime(2026, 10, 26))
    assert index.get_original_source_evidence('小天', ['sync:' + uid]) == {}


@pytest.mark.parametrize('role', ['tool', 'function', 'unknown', 'system'])
def test_original_source_lookup_rejects_persisted_non_chat_roles(index, role):
    from datetime import datetime
    uid = 'c'*32
    index.store_conversation_strict('non-chat-proof', [{'type': role, 'data': {
        'content': 'non-chat original', 'metadata': {'recent_memory_id': uid, 'recent_memory_time': '2000'}}}],
        '小天', datetime(2026, 10, 26))
    assert index.get_original_source_evidence('小天', ['sync:' + uid]) == {}


@pytest.mark.asyncio
async def test_legacy_original_correction_time_recovers_outside_current_window(index, tmp_path):
    from datetime import datetime
    from pathlib import Path
    from sqlalchemy import text
    from app.memory_server.rows import _extract_role_tagged_messages_from_rows
    from utils.llm_client import HumanMessage, convert_to_messages
    index.store_conversation_strict('legacy-correction', [HumanMessage(content='real old correction')],
        '小天', datetime(2026, 10, 1))
    with index.engines['小天'].connect() as connection:
        originals = list(connection.execute(text('SELECT timestamp,session_id,message FROM time_indexed_original')))
    correction = convert_to_messages(_extract_role_tagged_messages_from_rows(originals))[0].additional_kwargs
    identity, at = correction['memory_source_id'], correction['memory_source_created_at']
    assert identity.startswith('desktop:')
    directory = _portable_retirement(tmp_path, correction=True)
    path = directory / 'long_term_portable.json'
    state = json.loads(path.read_text())
    state['evidence']['retirements'][0]['retiredBySourceMessageIds'] = [identity]
    path.write_text(json.dumps(state))
    raw = Path(index.db_paths['小天'])
    before = raw.read_bytes()
    h = _harness(tmp_path, [])
    h._time_indexed.aget_original_source_evidence = lambda name, ids: index.aget_original_source_evidence(name, ids)
    candidate = {'entity': 'master', 'text': '用户喜欢红茶', 'source_indices': [1]}
    newer = [_trusted_sync_message('b', at + 1000)]
    assert len(await h._apersist_new_facts('小天', [candidate], source_messages=newer)) == 1
    assert json.loads(path.read_text())['evidence']['retirements'][0]['retiredThroughAt'] == at
    assert await h._apersist_new_facts('小天', [candidate], source_messages=newer) == []
    reopened = _harness(tmp_path, [])
    assert await reopened._apersist_new_facts('小天', [candidate], source_messages=[_trusted_sync_message('d', at - 1000)]) == []
    assert raw.read_bytes() == before


@pytest.mark.asyncio
async def test_correction_time_write_failure_can_retry_without_duplicate_fact(tmp_path, monkeypatch):
    from tests.unit.test_evidence_archive import _install
    from utils import file_utils
    directory = _portable_retirement(tmp_path, correction=True)
    path = directory / 'long_term_portable.json'
    before = path.read_bytes()
    _ev, fs, _pm, _re, _rec, cm = _install(str(tmp_path))
    cm.app_docs_dir, cm.local_state_dir = tmp_path, tmp_path / 'local_state'
    cm.local_state_dir.mkdir()
    original = file_utils.atomic_write_json

    def unavailable(target, *args, **kwargs):
        if target.name == 'long_term_portable.json':
            raise OSError('evidence write unavailable')
        return original(target, *args, **kwargs)

    messages = [_trusted_sync_message('c', 2000), _trusted_sync_message('b', 3000)]
    candidate = {'entity': 'master', 'text': '用户喜欢红茶', 'source_indices': [2]}
    monkeypatch.setattr(file_utils, 'atomic_write_json', unavailable)
    with pytest.raises(OSError, match='evidence write unavailable'):
        await fs._apersist_new_facts('小天', [candidate], semantic_dedup=False, source_messages=messages)
    assert path.read_bytes() == before and not (directory / 'facts.json').exists()
    monkeypatch.setattr(file_utils, 'atomic_write_json', original)
    assert len(await fs._apersist_new_facts('小天', [candidate], semantic_dedup=False, source_messages=messages)) == 1
    assert await fs._apersist_new_facts('小天', [candidate], semantic_dedup=False, source_messages=messages) == []
    assert len(json.loads((directory / 'facts.json').read_text())) == 1


def test_legacy_source_lookup_ignores_unreadable_unrelated_rows_and_quoted_identities(index):
    from datetime import datetime
    from sqlalchemy import text
    from app.memory_server.rows import _extract_role_tagged_messages_from_rows
    from utils.llm_client import HumanMessage, convert_to_messages
    index.store_conversation_strict('legacy-source', [HumanMessage(content='real legacy correction')],
        '小天', datetime(2026, 10, 1))
    with index.engines['小天'].connect() as connection:
        originals = list(connection.execute(text('SELECT timestamp,session_id,message FROM time_indexed_original')))
    proof = convert_to_messages(_extract_role_tagged_messages_from_rows(originals))[0].additional_kwargs
    identity, at = proof['memory_source_id'], proof['memory_source_created_at']
    sync_id = 'sync:' + 'd'*32
    quoted_id = 'desktop:' + 'e'*64
    index.store_conversation_strict('other-originals', [
        HumanMessage(content='independent original', metadata={'recent_memory_id': 'd'*32, 'recent_memory_time': '1000'}),
        HumanMessage(content=identity + ' ' + quoted_id + ' sync:' + 'f'*32),
    ], '小天', datetime(2026, 10, 26))
    with index.engines['小天'].begin() as connection:
        connection.execute(text('INSERT INTO time_indexed_original(session_id,message,timestamp) VALUES(:session,:message,:at)'),
            {'session': 'unreadable-unrelated', 'message': '{broken', 'at': '2026-10-26'})
    assert index.get_original_source_evidence('小天', [identity, sync_id, quoted_id, 'sync:' + 'f'*32]) == {
        identity: ('human', at), sync_id: ('human', 1000)}


@pytest.mark.parametrize('portable_identity', [True, False])
@pytest.mark.parametrize('stored_at,expected', [
    ('', None), (None, None), (' ', None), ('unreadable time', None), (b'', None),
    (0, 0), ('1970-01-01T00:00:00Z', 0), ('2026-10-01T12:00:00+08:00', 1790827200000),
])
def test_original_database_time_preserves_unknown_and_epoch_zero(index, stored_at, expected, portable_identity):
    from datetime import datetime
    from pathlib import Path
    from sqlalchemy import text
    from app.memory_server.rows import _extract_role_tagged_messages_from_rows
    from utils.llm_client import HumanMessage, convert_to_messages
    metadata = {'recent_memory_id': 'c'*32} if portable_identity else {}
    index.store_conversation_strict('source-clock', [HumanMessage(content='real original', metadata=metadata)],
        '小天', datetime(2026, 10, 1))
    with index.engines['小天'].begin() as connection:
        connection.execute(text('UPDATE time_indexed_original SET timestamp=:at WHERE session_id=:session'),
            {'at': stored_at, 'session': 'source-clock'})
    raw = Path(index.db_paths['小天'])
    before = raw.read_bytes()
    with index.engines['小天'].connect() as connection:
        originals = list(connection.execute(text('SELECT timestamp,session_id,message FROM time_indexed_original')))
    source = convert_to_messages(_extract_role_tagged_messages_from_rows(originals))[0].additional_kwargs
    identity = source['memory_source_id']
    assert identity.startswith('sync:' if portable_identity else 'desktop:')
    assert source == {'memory_source_id': identity, **({'memory_source_created_at': expected} if expected is not None else {})}
    assert index.get_original_source_evidence('小天', [identity]) == (
        {identity: ('human', expected)} if expected is not None else {})
    assert raw.read_bytes() == before


@pytest.mark.parametrize('original_time', ['0', '1970-01-01T00:00:00Z', '1970-01-01T08:00:00+08:00'])
def test_explicit_epoch_zero_evidence_survives_blank_database_time(index, original_time):
    from datetime import datetime
    from sqlalchemy import text
    from utils.llm_client import HumanMessage
    identity = 'c'*32
    index.store_conversation_strict('explicit-zero', [HumanMessage(content='epoch original', metadata={
        'chat_original_source': {'recordId': identity, 'originalTime': original_time}})], '小天', datetime(2026, 10, 1))
    with index.engines['小天'].begin() as connection:
        connection.execute(text("UPDATE time_indexed_original SET timestamp='' WHERE session_id='explicit-zero'"))
    assert index.get_original_source_evidence('小天', ['sync:' + identity]) == {'sync:' + identity: ('human', 0)}


@pytest.mark.asyncio
async def test_unknown_correction_clock_waits_then_recovers_real_epoch_zero_without_recounting(index, tmp_path):
    from datetime import datetime
    from pathlib import Path
    from sqlalchemy import text
    from tests.unit.test_evidence_archive import _install
    from utils.llm_client import HumanMessage
    from memory.long_term_sync import FactEvidenceUnavailable, read_views, export_document, import_document
    identity = 'c'*32
    index.store_conversation_strict('recover-clock', [HumanMessage(content='real correction',
        metadata={'recent_memory_id': identity})], '小天', datetime(2026, 10, 1))
    with index.engines['小天'].begin() as connection:
        connection.execute(text("UPDATE time_indexed_original SET timestamp='' WHERE session_id='recover-clock'"))
    directory = _portable_retirement(tmp_path, correction=True)
    path = directory / 'long_term_portable.json'
    before = path.read_bytes()
    raw = Path(index.db_paths['小天'])
    raw_before = raw.read_bytes()
    _ev, fs, _pm, _re, _rec, cm = _install(str(tmp_path))
    cm.app_docs_dir, cm.local_state_dir = tmp_path, tmp_path / 'local_state'
    fs._time_indexed = _FakeIndex()
    fs._time_indexed.aget_original_source_evidence = lambda name, ids: index.aget_original_source_evidence(name, ids)
    candidate = {'entity': 'master', 'text': '用户喜欢红茶', 'source_indices': [1]}
    messages = [_trusted_sync_message('b', 3000, '用户喜欢红茶')]
    for _ in range(2):
        with pytest.raises(FactEvidenceUnavailable):
            await fs._apersist_new_facts('小天', [candidate], semantic_dedup=False, source_messages=messages)
    assert path.read_bytes() == before and raw.read_bytes() == raw_before
    assert not (directory / 'facts.json').exists()

    with index.engines['小天'].begin() as connection:
        connection.execute(text('UPDATE time_indexed_original SET timestamp=0 WHERE session_id=:session'),
            {'session': 'recover-clock'})
    repaired_raw = raw.read_bytes()
    assert len(await fs._apersist_new_facts('小天', [candidate], semantic_dedup=False, source_messages=messages)) == 1
    assert await fs._apersist_new_facts('小天', [candidate], semantic_dedup=False, source_messages=messages) == []
    assert await fs._apersist_new_facts('小天', [candidate], semantic_dedup=False,
        source_messages=[_trusted_sync_message('d', -1000)]) == []
    facts = json.loads((directory / 'facts.json').read_text())
    assert len(facts) == 1 and facts[0]['signal_processed'] is False
    portable = export_document(read_views(directory), 4000)
    restored = import_document(portable, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    retirement = restored['sync']['evidence']['retirements'][0]
    assert retirement['retiredThroughAt'] == 0 and retirement['retiredThroughAtFromCorrection'] is True
    assert raw.read_bytes() == repaired_raw


@pytest.mark.asyncio
async def test_committed_cancel_can_retry_failed_queue_write(tmp_path,monkeypatch):
    import threading
    from memory import facts
    from memory.fact_dedup import FactDedupResolver
    with patch('memory.facts.get_config_manager',return_value=_cm(str(tmp_path))):store=FactStore()
    row={'entity':'master','text':'user likes cats','source_indices':[1]}
    await store._apersist_new_facts('Review',[row],source_messages=[_trusted_sync_message('a',1000)])
    anchor=(await store.aload_facts('Review'))[0];store._time_indexed=_FakeIndex([(anchor['id'],.9)])
    resolver=FactDedupResolver(store);store.attach_dedup_resolver(resolver)
    entered,release=threading.Event(),threading.Event();original=facts.atomic_write_json
    async def fail_queue(*args):raise OSError('synthetic_queue_write_failure')
    monkeypatch.setattr(resolver,'_asave_pending',fail_queue)
    def blocked(path,*args,**kwargs):
        if str(path)==store._facts_path('Review'):
            entered.set();assert release.wait(3)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(facts,'atomic_write_json',blocked)
    candidate={**row,'text':'user enjoys cats'}
    task=asyncio.create_task(store._apersist_new_facts('Review',[candidate],source_messages=[_trusted_sync_message('b',2000)]))
    try:
        assert await asyncio.to_thread(entered.wait,2);task.cancel();await asyncio.sleep(0)
    finally:release.set();await asyncio.gather(task,return_exceptions=True)
    assert task.cancelled();assert len(await store.aload_facts('Review'))==2
    monkeypatch.setattr(facts,'atomic_write_json',original)
    # Reopen from disk: retry must not depend on an in-memory handoff.
    with patch('memory.facts.get_config_manager',return_value=_cm(str(tmp_path))):store=FactStore()
    resolver=FactDedupResolver(store);store.attach_dedup_resolver(resolver)
    await store._apersist_new_facts('Review',[candidate],source_messages=[_trusted_sync_message('b',2000)])
    pending=await resolver.aload_pending('Review')
    assert len(pending)==1

    await store._apersist_new_facts('Review',[candidate],source_messages=[_trusted_sync_message('b',2000)])
    assert len(await resolver.aload_pending('Review')) == 1
    assert all('_near_dup_pending' not in row for row in await store.aload_facts('Review'))


@pytest.mark.asyncio
async def test_failed_queue_acknowledgement_retries_before_resolution(tmp_path, monkeypatch):
    from memory.fact_dedup import FactDedupResolver
    with patch('memory.facts.get_config_manager', return_value=_cm(str(tmp_path))):
        store = FactStore()
    row = {'entity': 'master', 'text': 'user likes cats', 'source_indices': [1]}
    await store._apersist_new_facts('Review', [row], source_messages=[_trusted_sync_message('a', 1000)])
    anchor = (await store.aload_facts('Review'))[0]
    store._time_indexed = _FakeIndex([(anchor['id'], .9)])
    resolver = FactDedupResolver(store)
    store.attach_dedup_resolver(resolver)
    original = store.asave_facts
    calls = 0
    async def save(name):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError('synthetic_handoff_ack_failure')
        await original(name)
    monkeypatch.setattr(store, 'asave_facts', save)
    await store._apersist_new_facts('Review', [{**row, 'text': 'user enjoys cats'}], source_messages=[_trusted_sync_message('b', 2000)])
    stale_candidate = (await store.aload_facts('Review'))[-1]
    assert len(await resolver.aload_pending('Review')) == 1
    assert [fact['id'] for fact in await store.aget_unabsorbed_facts('Review', min_importance=0)] == [anchor['id']]
    resolved = []
    async def resolve(name, **kwargs):
        resolved.extend(await resolver.aload_pending(name))
        await resolver._asave_pending(name, [])
        return len(resolved)
    monkeypatch.setattr(resolver, '_aresolve_locked', resolve)
    assert await resolver.aresolve('Review') == 0
    assert not resolved
    monkeypatch.setattr(store, 'asave_facts', original)
    # Multiple retries may wait on the same queue lock. A stale retry must not requeue keep_both.
    await asyncio.wait_for(asyncio.gather(store.aretry_pending_near_dups('Review'), resolver.aresolve('Review'),
                                          store.aretry_pending_near_dups('Review')), timeout=3)
    assert len(resolved) == 1
    assert not await resolver.aload_pending('Review')
    assert await store.aretry_pending_near_dups('Review')
    assert not await resolver.aload_pending('Review')
    assert await store._aenqueue_near_dup_pairs('Review', [(stale_candidate, anchor, .9)], durable=True)
    assert not await resolver.aload_pending('Review')


@pytest.mark.asyncio
async def test_idle_recovers_committed_handoffs_when_strong_memory_is_disabled(monkeypatch):
    from types import SimpleNamespace
    from app.memory_server import evidence_loops as loops
    retry, resolve = AsyncMock(), AsyncMock()
    monkeypatch.setattr(loops.runtime, '_config_manager', SimpleNamespace(aload_characters=AsyncMock(return_value={'猫娘': {'Neko': {}}})))
    monkeypatch.setattr(loops.runtime, 'fact_store', SimpleNamespace(aretry_pending_near_dups=retry))
    monkeypatch.setattr(loops.runtime, 'fact_dedup_resolver', SimpleNamespace(aload_pending=AsyncMock(return_value=[{}])))
    monkeypatch.setattr(loops.runtime, 'recent_history_manager', SimpleNamespace(aget_recent_history=AsyncMock(return_value=[]), compress_threshold=20))
    monkeypatch.setattr(loops.gates, '_is_idle', lambda: True)
    monkeypatch.setattr(loops.gates, '_ais_powerful_memory_enabled', AsyncMock(return_value=False))
    monkeypatch.setattr(loops, '_resolve_fact_dedup_with_language', resolve)
    monkeypatch.setattr(loops, '_spawn_review_with_character_language', AsyncMock())
    sleeps = 0
    async def one_round(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError
    monkeypatch.setattr(loops.asyncio, 'sleep', one_round)
    with pytest.raises(asyncio.CancelledError):
        await loops._periodic_idle_maintenance_loop()
    retry.assert_awaited_once_with('Neko')
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_candidate_source_does_not_drop_valid_batch_members(tmp_path):
    from utils.llm_client import HumanMessage, AIMessage
    h = _harness(tmp_path, [])
    messages = [HumanMessage(content='I enjoy tea', additional_kwargs={'memory_source_id': 'raw-user', 'memory_source_created_at': 1000}),
                AIMessage(content='Maybe swimming', additional_kwargs={'memory_source_id': 'raw-ai', 'memory_source_created_at': 1001})]
    rows = await h._apersist_new_facts('小天', [
        {'entity': 'master', 'text': 'Enjoys tea', 'source_indices': [1]},
        {'entity': 'master', 'text': 'Invented user preference', 'source_indices': [2]},
        {'entity': 'master', 'text': 'Invalid index', 'source_indices': [99]},
    ], source_messages=messages)
    assert [row['text'] for row in rows] == ['Enjoys tea']
    assert rows[0]['source_message_ids'] == ['raw-user']


@pytest.mark.asyncio
async def test_retirement_model_review_releases_persistence_lock_and_rechecks_evidence(tmp_path):
    from utils.llm_client import HumanMessage
    from memory.long_term_sync import FactEvidenceUnavailable
    h = _harness(tmp_path, [])
    directory = tmp_path / '小天'
    directory.mkdir(exist_ok=True)
    path = directory / 'long_term_portable.json'
    path.write_text(json.dumps({'evidence': {'retirements': [
        {'id': 'old', 'subject': 'master', 'text': 'old assertion', 'sourceMessageIds': ['raw']}
    ], 'confirmations': []}}))
    entered, release = asyncio.Event(), asyncio.Event()
    async def review(*args, **kwargs):
        assert not h._get_persist_alock('小天').locked()
        entered.set()
        await release.wait()
        return [{'index': 0, 'action': 'keep_both'}]
    h._allm_call_with_retries = review
    source = HumanMessage(content='new claim', additional_kwargs={'memory_source_id': 'raw', 'memory_source_created_at': 1000})
    task = asyncio.create_task(h._apersist_new_facts('小天', [{'entity': 'master', 'text': 'new claim', 'source_indices': [1]}], source_messages=[source]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        async with h._get_persist_alock('小天'):
            path.write_text(json.dumps({'evidence': {'retirements': [], 'confirmations': []}}))
        release.set()
        with pytest.raises(FactEvidenceUnavailable):
            await task
        assert h._mem == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
