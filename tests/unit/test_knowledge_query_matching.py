"""Protect title, symbol, coverage and passage matching across writing systems.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from services.knowledge import querying as query_module
from services.knowledge.text import fts_match_expression, search_tokens
from tests.knowledge_fixtures import _entries, _import, _pack, _started

pytestmark = pytest.mark.unit


async def test_lookup_filters_material_type_before_the_candidate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(query_module, "LEXICAL_CANDIDATES", 2)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack("corpus-pack", material_type="corpus", entries=_entries("c", 5)))
        await _import(
            service,
            _pack("fact-pack", entries=[{"title": "Fact", "content": "a kotatsu fact"}]),
        )
        result = await service.query(query="kotatsu", material_type="knowledge")
        assert result["result"] == "matched"
        assert [hit["pack_id"] for hit in result["hits"]] == ["fact-pack"]
    finally:
        await service.stop()


async def test_lookup_renders_the_matching_passage_of_a_long_entry(tmp_path):
    filler = "\n\n".join(f"Paragraph {i}: " + "lorem ipsum dolor " * 40 for i in range(6))
    entry = {
        "title": "Long article",
        "content": filler + "\n\nThe kotatsu heater is described here in detail.",
    }
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=[entry]))
        result = await service.query(query="kotatsu heater", language="en")
        assert result["result"] == "matched"
        assert "kotatsu heater" in result["context"]
        assert "Paragraph 0" not in result["context"]
    finally:
        await service.stop()


def test_semantic_match_reports_the_best_chunk_and_scales_with_many_packs():
    from services.knowledge.retrieval import semantic_candidates
    from services.knowledge.store import VectorSnapshot

    class CountingPackIds(tuple):
        """Counts per-pack lookups; a linear filter needs none of them."""

        lookups = 0

        def index(self, *args):
            CountingPackIds.lookups += 1
            return super().index(*args)

        def __contains__(self, item):
            CountingPackIds.lookups += 1
            return super().__contains__(item)

    count = 2_000
    matrix = np.zeros((count, 4), dtype=np.float32)
    matrix[:, 0] = 1.0
    matrix[-1] = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    snapshot = VectorSnapshot(
        model_id="m",
        entry_ids=np.arange(count, dtype=np.int64),
        pack_ids=CountingPackIds(f"pack-{i:05d}" for i in range(count)),
        chunk_pack_index=np.arange(count, dtype=np.int32),
        matrix=matrix,
        chunk_indexes=np.full(count, 3, dtype=np.int32),
    )
    matches = semantic_candidates(
        snapshot,
        np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32),
        allowed_pack_ids=list(snapshot.pack_ids),
    )
    # Filtering by pack must not search the snapshot tuple once per pack.
    assert CountingPackIds.lookups == 0
    assert [(m.entry_id, m.chunk_index) for m in matches] == [(count - 1, 3)]


async def test_the_tail_of_a_long_entry_is_searchable_and_rendered(tmp_path):
    from services.knowledge.chunking import MAX_CHUNKS_PER_ENTRY, chunk_bodies

    # Short and long paragraphs alternate, so paragraph-aware chunking needs
    # more than MAX_CHUNKS_PER_ENTRY chunks and the even-window path is used.
    paragraphs = []
    for i in range(60):
        paragraphs.append(f"Note {i}. " + "short " * 15)
        paragraphs.append(f"Paragraph {i}: " + "lorem ipsum dolor " * 63)
    content = "\n\n".join(paragraphs + ["Finally the zanzibar detail lives at the very end."])
    assert 60_000 < len(content) < 80_000
    from services.knowledge.chunking import _bodies

    assert len(_bodies(content)) > MAX_CHUNKS_PER_ENTRY
    bodies = chunk_bodies(content)
    assert len(bodies) <= MAX_CHUNKS_PER_ENTRY
    assert "zanzibar" in bodies[-1]
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=[{"title": "Very long", "content": content}]))
        result = await service.query(query="zanzibar detail", language="en")
        assert result["result"] == "matched"
        assert "zanzibar" in result["context"]
    finally:
        await service.stop()


def test_latin_coverage_counts_whole_words_only():
    from services.knowledge.retrieval import token_coverage
    from services.knowledge.store import StoredEntry

    entry = StoredEntry(
        entry_id=1, pack_id="p", title="Cats", terms={}, tags=[], summary="",
        content="the cat sat", disabled=False,
    )
    assert token_coverage("he cat", entry) == 0.5
    assert token_coverage("猫咪 cat", StoredEntry(
        entry_id=2, pack_id="p", title="t", terms={}, tags=[], summary="",
        content="一只猫咪 cat", disabled=False,
    )) == 1.0


async def test_exact_matches_keep_meaningful_symbols(tmp_path):
    entries = [
        {"title": "C", "content": "The C language."},
        {"title": "C++", "content": "The C++ language."},
        {"title": "C#", "content": "The C# language."},
        {"title": "😂", "content": "Face with tears of joy."},
        {"title": "绝绝子", "terms": {"recognition": ["绝绝子是什么意思"]}, "content": "网络用语。"},
    ]
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=entries))
        store = service._store
        rows = await asyncio.to_thread(store.list_entries, limit=10, offset=0)
        by_title = {row.title: row.entry_id for row in rows}

        def exact(query):
            return store.lexical_candidates(query, pack_ids=["demo-memes"], limit=10)[0]

        assert exact("C") == [by_title["C"]]
        assert exact("c++") == [by_title["C++"]]
        assert exact("😂") == [by_title["😂"]]
        # Loose fallback: different punctuation still finds the phrase.
        assert exact("绝绝子是什么意思？") == [by_title["绝绝子"]]
    finally:
        await service.stop()


async def test_supplementary_han_characters_are_bigram_indexed(tmp_path):
    word = "\U00020000\U00020001\U00020002"
    assert search_tokens(word) == [word[0:2], word[1:3]]
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=[{"title": "Rare", "content": f"text {word} text"}]))
        assert (await service.query(query=word[1:3]))["result"] == "matched"
    finally:
        await service.stop()


async def test_a_question_naming_the_title_matches_without_vectors(tmp_path):
    entry = {"title": "Python", "content": "A programming language."}
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=[entry]))
        result = await service.query(query="Tell me about Python", language="en")
        assert result["result"] == "matched"
        assert result["retrieval_mode"] == "bm25"
    finally:
        await service.stop()


async def test_single_character_cjk_name_inside_a_question(tmp_path):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=[{"title": "猫", "content": "一种小型哺乳动物。"}]))
        assert (await service.query(query="介绍一下猫"))["result"] == "matched"
    finally:
        await service.stop()


def test_long_queries_keep_terms_from_their_tail():
    words = " ".join(f"w{i}" for i in range(400)) + " zanzibar"
    expression = fts_match_expression(words)
    assert '"zanzibar"' in expression
    assert expression.count(" OR ") + 1 <= 128


async def test_symbol_bearing_names_have_no_loose_exact_form(tmp_path):
    entries = [
        {"title": "C++", "content": "The C++ language."},
        {"title": "Python", "content": "Another language."},
        {"title": "Re:Zero", "content": "A light novel."},
        {"title": "猫", "content": "一种小型哺乳动物。"},
    ]
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=entries))
        store = service._store
        rows = await asyncio.to_thread(store.list_entries, limit=10, offset=0)
        by_title = {row.title: row.entry_id for row in rows}

        def exact(query):
            return store.lexical_candidates(query, pack_ids=["demo-memes"], limit=10)[0]

        assert exact("C") == []
        assert exact("C++") == [by_title["C++"]]
        assert exact("Python?") == [by_title["Python"]]
        assert exact("re zero") == [by_title["Re:Zero"]]
        assert exact("「猫」") == [by_title["猫"]]
    finally:
        await service.stop()


def test_loose_surface_rules():
    from services.knowledge.text import loose_surface

    assert loose_surface("C++") == ""
    assert loose_surface("C#") == ""
    assert loose_surface(".NET") == ""
    assert loose_surface("Hello, World!") == "helloworld"
    assert loose_surface("re zero") == loose_surface("Re:Zero") == "rezero"
    assert loose_surface('"Python"') == loose_surface("'Python'") == "python"
    assert loose_surface('"C++"') == ""
    # A lone straight quote belongs to the name.
    assert loose_surface("Lil'") == loose_surface("'Tis") == ""
    assert loose_surface('"Python"?') == "python"
    # Curly quotes too: a pair goes, a lone apostrophe stays.
    assert loose_surface("\u201cPython\u201d") == loose_surface("\u00abPython\u00bb") == "python"
    assert loose_surface("Lil\u2019") == loose_surface("\u2018Tis") == ""
    # A symbol inside the name also makes it another name.
    assert loose_surface("AT&T") == loose_surface("C++17") == ""
    assert loose_surface("AT T") == "att"


def test_coverage_scoring_is_bounded_for_long_queries(monkeypatch):
    from services.knowledge import retrieval
    from services.knowledge.store import StoredEntry
    from services.knowledge.text import MAX_QUERY_TOKENS

    checked = []
    real = retrieval.is_cjk_token

    def counting(token):
        checked.append(token)
        return real(token)

    monkeypatch.setattr(retrieval, "is_cjk_token", counting)
    query = "".join(chr(0x4E00 + i) for i in range(2000))
    entry = StoredEntry(
        entry_id=1, pack_id="p", title="t", summary="", content="x", terms={}, tags=(), disabled=False
    )
    retrieval.token_coverage(query, entry)
    assert 0 < len(checked) <= MAX_QUERY_TOKENS


def test_names_in_questions_respect_symbols():
    from services.knowledge.retrieval import names_in_query
    from services.knowledge.store import StoredEntry

    def entry(title):
        return StoredEntry(
            entry_id=1, pack_id="p", title=title, terms={}, tags=[], summary="", content="", disabled=False
        )

    assert names_in_query("C", entry("C++")) is False
    assert names_in_query("Tell me about C++", entry("C++")) is True
    assert names_in_query("is C# hard?", entry("C#")) is True
    assert names_in_query("C++ tutorial", entry("C")) is False
    assert names_in_query("C++tutorial", entry("C")) is False
    assert names_in_query("C#developer", entry("C")) is False
    assert names_in_query("AT T", entry("AT&T")) is False
    assert names_in_query("is AT&T a carrier?", entry("AT&T")) is True
    assert names_in_query("re:zero season 2", entry("Re:Zero")) is True
    assert names_in_query("x-ray machines", entry("X-ray")) is True
    assert names_in_query("Tell me about Python", entry("Python")) is True
    assert names_in_query('What is "Python"?', entry("Python")) is True
    assert names_in_query("Lil", entry("Lil'")) is False
    assert names_in_query("who is Lil'?", entry("Lil'")) is True
    assert names_in_query("is `C++` hard?", entry("C++")) is True
    assert names_in_query("is `Python` slow?", entry("Python")) is True
    assert names_in_query("C+++", entry("C++")) is False
    assert names_in_query("$$X", entry("$X")) is False
    assert names_in_query("about $X today", entry("$X")) is True
    # Multiword names must appear as a phrase.
    assert names_in_query("trip to new york", entry("New York")) is True
    assert names_in_query("York and New Jersey", entry("New York")) is False
    assert names_in_query("new c++ york", entry("New York")) is False
    assert names_in_query("介绍一下猫", entry("猫")) is True


def test_coverage_ignores_words_glued_to_symbols():
    from services.knowledge.retrieval import token_coverage
    from services.knowledge.store import StoredEntry

    def entry(title, content):
        return StoredEntry(
            entry_id=1, pack_id="p", title=title, terms={}, tags=[], summary="", content=content, disabled=False
        )

    cpp = entry("C++", "The C++ language.")
    assert token_coverage("C", cpp) == 0.0
    assert token_coverage("C++ language", cpp) == 1.0  # "language" still counts
    assert token_coverage("C++", entry("C", "The C language.")) == 0.0
    assert token_coverage("C language", entry("C", "The C language.")) == 1.0


async def test_a_bare_letter_does_not_match_a_symbol_name_without_vectors(tmp_path):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=[{"title": "C++", "content": "The C++ language."}]))
        assert (await service.query(query="C"))["result"] == "miss"
        assert (await service.query(query="C++"))["result"] == "matched"
    finally:
        await service.stop()


def test_cjk_coverage_ignores_runs_glued_to_symbols():
    from services.knowledge.retrieval import token_coverage
    from services.knowledge.store import StoredEntry

    def entry(title, content):
        return StoredEntry(
            entry_id=1, pack_id="p", title=title, terms={}, tags=[], summary="", content=content, disabled=False
        )

    cat = chr(0x732B)
    food = chr(0x7CAE)
    assert token_coverage(cat, entry(cat + "++", cat + "++ " + "x")) == 0.0
    assert token_coverage(cat, entry(cat + food, cat + food)) == 1.0  # substring inside a run


def test_accents_tell_strict_surfaces_apart():
    from services.knowledge.text import loose_surface, strict_surface

    plain, accented = "resume", "résumé"
    assert strict_surface(plain) != strict_surface(accented)
    assert loose_surface(plain) == loose_surface(accented) == "resume"


async def test_an_unaccented_query_prefers_the_literal_title(tmp_path):
    entries = [
        {"title": "resume", "content": "a summary of a career"},
        {"title": "résumé", "content": "accented spelling"},
    ]
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=entries))
        store = service._store
        rows = await asyncio.to_thread(store.list_entries, limit=10, offset=0)
        by_title = {row.title: row.entry_id for row in rows}
        exact = store.lexical_candidates("resume", pack_ids=["demo-memes"], limit=10)[0]
        assert exact == [by_title["resume"]]
    finally:
        await service.stop()


async def test_sampling_matches_whole_tags_ignoring_case(tmp_path):
    entries = [
        {"title": "Longer tag", "tags": ["snacks"], "content": "one"},
        {"title": "Quoted tag", "tags": ['x", "snack'], "content": "two"},
        {"title": "Exact tag", "tags": ["Snack"], "content": "three"},
    ]
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=entries))
        rows = await asyncio.to_thread(service._store.entries_with_tag, "SNACK", ["demo-memes"])
        assert [title for _id, _pack_id, title in rows] == ["Exact tag"]
    finally:
        await service.stop()
