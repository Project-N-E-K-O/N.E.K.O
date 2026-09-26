from __future__ import annotations

import json
import random

import pytest

from knowledge.api import KnowledgeEntry, KnowledgeStore, open_knowledge
from knowledge.catalog_overrides import (
    CatalogOverrideError,
    get_catalog_override_path,
    load_disabled_entries,
    set_entry_disabled,
)
from knowledge.packs import validate_pack
from knowledge.service import (
    KnowledgeCardIdentity,
    MaterialKnowledgeHit,
    _AUTOMATIC_CONTEXT_CLOSING_FENCE,
    _AUTOMATIC_CONTEXT_MAX_CHARS,
    _is_direct_material_match,
    _is_short_query_embedded_in_term,
)
from knowledge.models import KnowledgeHit


def _entry(title: str, source: str, *tags: str) -> KnowledgeEntry:
    return KnowledgeEntry(
        title=title,
        terms={"alias": (f"{title} alias",), "recognition": ()},
        tags=(source, *tags),
        summary=f"Meaning of {title}",
        content=f"Reference for {title}",
    )


def test_invalid_catalog_override_fails_closed_and_is_not_overwritten(tmp_path):
    service = open_knowledge(tmp_path)
    service.install_pack(
        _pack(
            pack_id="override-fixture",
            material_type="knowledge",
            title="Disabled fixture",
        )
    )
    override_path = get_catalog_override_path(service.database_path())
    corrupt = b'{"disabled": ['
    override_path.write_bytes(corrupt)

    with pytest.raises(CatalogOverrideError):
        load_disabled_entries(override_path)
    with pytest.raises(CatalogOverrideError):
        service.set_entry_disabled(
            source_tag="source:community.override-fixture",
            title="Disabled fixture",
            disabled=True,
        )

    assert override_path.read_bytes() == corrupt
    assert service.build_turn_context("Disabled fixture").hit_count == 0
    status = service.get_status()
    assert status["catalog_override_state"] == "invalid"
    assert status["integrity_ok"] is False


def test_non_utf8_catalog_override_is_reported_as_invalid(tmp_path):
    service = open_knowledge(tmp_path)
    override_path = get_catalog_override_path(service.database_path())
    override_path.write_bytes(b"\xff\xfe")

    with pytest.raises(CatalogOverrideError):
        load_disabled_entries(override_path)
    assert service.get_status()["catalog_override_state"] == "invalid"


def test_catalog_override_reader_rejects_oversize_valid_json(monkeypatch, tmp_path):
    import knowledge.catalog_overrides as overrides

    override_path = get_catalog_override_path(tmp_path / "knowledge.db")
    override_path.write_text(
        json.dumps(
            {
                "disabled": [
                    {"source": "source:fixture", "title": "Disabled fixture"}
                ]
            }
        ),
        encoding="utf-8",
    )
    assert override_path.stat().st_size > 32
    monkeypatch.setattr(overrides, "MAX_CATALOG_OVERRIDE_BYTES", 32)

    with pytest.raises(CatalogOverrideError, match="unreadable or invalid"):
        load_disabled_entries(override_path)


def test_catalog_override_writer_rejects_oversize_before_publish(
    monkeypatch,
    tmp_path,
):
    import knowledge.catalog_overrides as overrides

    override_path = get_catalog_override_path(tmp_path / "knowledge.db")
    monkeypatch.setattr(overrides, "MAX_CATALOG_OVERRIDE_BYTES", 32)

    with pytest.raises(CatalogOverrideError, match="size limit"):
        set_entry_disabled(
            override_path,
            source_tag="source:fixture",
            title="Disabled fixture",
            disabled=True,
        )

    assert not override_path.exists()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("source", False),
        ("source", 1),
        ("source", []),
        ("source", {}),
        ("title", False),
        ("title", 1),
        ("title", []),
        ("title", {}),
    ),
)
def test_catalog_override_rejects_non_string_identity_without_rewriting(
    tmp_path,
    field,
    invalid_value,
):
    service = open_knowledge(tmp_path)
    override_path = get_catalog_override_path(service.database_path())
    payload = {
        "disabled": [
            {"source": "source:fixture", "title": "Disabled fixture"}
        ]
    }
    payload["disabled"][0][field] = invalid_value
    override_path.write_text(json.dumps(payload), encoding="utf-8")
    before = override_path.read_bytes()

    with pytest.raises(CatalogOverrideError):
        load_disabled_entries(override_path)
    with pytest.raises(CatalogOverrideError):
        service.set_entry_disabled(
            source_tag="source:fixture",
            title="Disabled fixture",
            disabled=True,
        )

    assert service.get_status()["catalog_override_state"] == "invalid"
    assert override_path.read_bytes() == before


@pytest.mark.parametrize("field", ("source", "title"))
def test_catalog_override_setter_rejects_non_string_identity(tmp_path, field):
    arguments = {
        "path": tmp_path / "catalog-overrides.json",
        "source_tag": "source:fixture",
        "title": "Disabled fixture",
        "disabled": True,
    }
    arguments["source_tag" if field == "source" else "title"] = 1

    with pytest.raises(ValueError, match="source and title are required"):
        set_entry_disabled(**arguments)

    assert not arguments["path"].exists()


def test_disabled_identity_survives_equivalent_pack_title_update(tmp_path):
    service = open_knowledge(tmp_path)
    pack_id = "normalized-override"
    source_tag = f"source:community.{pack_id}"
    service.install_pack(
        _pack(pack_id=pack_id, material_type="knowledge", title="Straße  Term")
    )
    service.set_pack_auto_context(pack_id, enabled=True)
    service.set_entry_disabled(
        source_tag=source_tag,
        title="Straße  Term",
        disabled=True,
    )

    service.install_pack(
        _pack(pack_id=pack_id, material_type="knowledge", title="STRASSE Term")
    )

    normalized_key = (source_tag, "strasse term")
    override_path = get_catalog_override_path(service.database_path())
    assert load_disabled_entries(override_path) == frozenset({normalized_key})
    assert service.search("STRASSE Term", limit=1) == []
    assert service.build_conversation_context("STRASSE Term").hit_count == 0
    assert KnowledgeStore(service.database_path()).entry_rowids_for_keys(
        (normalized_key,)
    )

    service.set_entry_disabled(
        source_tag=source_tag,
        title="ＳＴＲＡＳＳＥ\nTerm",
        disabled=False,
    )
    assert service.search("STRASSE Term", limit=1)[0].entry.title == "STRASSE Term"


def test_fresh_empty_knowledge_root_is_healthy_without_creating_database(tmp_path):
    service = open_knowledge(tmp_path)

    status = service.get_status()

    assert status["integrity_ok"] is True
    assert status["entries"] == 0
    assert not service.database_path().exists()


def test_missing_installed_database_is_degraded_without_recreation(
    tmp_path,
    monkeypatch,
):
    service = open_knowledge(tmp_path)
    monkeypatch.setattr(service, "refresh_routing_index", lambda **_kwargs: None)
    service.install_pack(
        _pack(
            pack_id="missing-database",
            material_type="knowledge",
            title="Missing database fixture",
        )
    )
    database_path = service.database_path()
    database_path.unlink()

    status = service.get_status()

    assert status["integrity_ok"] is False
    assert status["schema_state"] == "invalid_or_unavailable"
    assert status["error_code"] == "knowledge_database_missing"
    assert status["packs"] == 1
    assert status["entries"] == status["chunks_total"] == 0
    assert not database_path.exists()


@pytest.mark.parametrize("with_registered_pack", (False, True))
def test_unregistered_community_rows_degrade_management_health(
    tmp_path,
    monkeypatch,
    with_registered_pack,
):
    service = open_knowledge(tmp_path)
    monkeypatch.setattr(service, "refresh_routing_index", lambda **_kwargs: None)
    if with_registered_pack:
        service.install_pack(
            _pack(
                pack_id="registered-fixture",
                material_type="knowledge",
                title="Registered fixture",
            )
        )
    KnowledgeStore(service.database_path()).upsert(
        _entry("Orphaned fixture", "source:community.orphaned-fixture")
    )

    status = service.get_status()

    assert status["integrity_ok"] is False
    assert status["pack_registry_state"] == (
        "ready" if with_registered_pack else "missing"
    )
    assert status["entries"] == (2 if with_registered_pack else 1)
    assert status["knowledge_entries"] == (1 if with_registered_pack else 0)
    assert status["corpus_entries"] == 0


def test_lexical_exact_match_preserves_meaningful_punctuation(tmp_path):
    service = open_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path())
    store.upsert(_entry("C++", "source:fixture"))
    store.upsert(_entry("C#", "source:fixture"))

    assert service.search("C++", limit=1)[0].entry.title == "C++"
    assert service.search("C#", limit=1)[0].entry.title == "C#"


def test_punctuated_exact_match_is_recalled_before_broad_candidate_cap(tmp_path):
    service = open_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path())
    for index in range(13):
        store.upsert(_entry(f"C {index:02d}", "source:fixture"))
    store.upsert(_entry("C++", "source:fixture"))

    result = service.search("C++", limit=1)

    assert result[0].entry.title == "C++"


def test_unicode_folded_exact_match_is_recalled_before_broad_candidate_cap(tmp_path):
    service = open_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path())
    for index in range(13):
        store.upsert(_entry(f"STRASSE {index:02d}", "source:fixture"))
    store.upsert(_entry("Straße", "source:fixture"))
    alias_entry = KnowledgeEntry(
        title="Measured concept",
        terms={"alias": ("Maße",), "recognition": ()},
        tags=("source:fixture",),
        summary="Unicode alias",
        content="Unicode alias fixture",
    )
    store.upsert(alias_entry)

    assert service.search("STRASSE", limit=1)[0].entry.title == "Straße"
    assert service.search("MASSE", limit=1)[0].entry.title == "Measured concept"


def test_search_page_uses_one_stable_ranked_window(tmp_path, monkeypatch):
    service = open_knowledge(tmp_path)
    ranked = [
        KnowledgeHit(entry=_entry(f"Result {index}", "source:fixture"), score=1.0)
        for index in range(120)
    ]
    requested_windows: list[tuple[int, int]] = []

    class _Retriever:
        def search(self, _query, *, limit, candidate_limit_cap, **_kwargs):
            requested_windows.append((limit, candidate_limit_cap))
            return ranked[:limit]

    monkeypatch.setattr(service, "_retriever", lambda: _Retriever())

    first = service.search_page("query", limit=50, offset=0)
    second = service.search_page("query", limit=50, offset=50)

    assert requested_windows == [(10_101, 10_101), (10_101, 10_101)]
    assert [hit.entry.title for hit in first[-2:]] == ["Result 49", "Result 50"]
    assert [hit.entry.title for hit in second[:2]] == ["Result 50", "Result 51"]


def test_empty_and_populated_status_share_chunk_fields(tmp_path):
    empty_status = open_knowledge(tmp_path / "empty").get_status()
    populated_service = open_knowledge(tmp_path / "populated")
    populated_service._store().upsert(_entry("Status fixture", "source:fixture"))
    populated_status = populated_service.get_status()
    chunk_fields = {
        key
        for key in populated_status
        if key.startswith("chunks_") or key.startswith("entries_")
    }

    assert chunk_fields <= empty_status.keys()
    assert all(empty_status[key] == 0 for key in chunk_fields)


def test_corrupt_database_status_is_structured_degraded(tmp_path):
    service = open_knowledge(tmp_path)
    service.database_path().write_bytes(b"not a sqlite database")

    status = service.get_status()

    assert status["integrity_ok"] is False
    assert status["schema_state"] == "invalid_or_unavailable"
    assert status["error_code"] == "knowledge_database_unavailable"
    assert status["entries"] == status["chunks_total"] == 0
    assert status["chunks_local"] == status["chunks_prebuilt_only"] == 0


def test_sample_entries_draws_from_complete_enabled_tag_population(
    tmp_path,
    monkeypatch,
):
    service = open_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path())
    tag = "dataset:tarot-interpretations"
    store.upsert_many(
        tuple(
            _entry(f"card {index:03d}", "source:corpora", tag)
            for index in range(101)
        )
    )
    monkeypatch.setattr(random, "randrange", lambda _population: 0)

    selected = service.sample_entries(tag, limit=1)

    assert selected[0].title == "card 100"
    service.set_entry_disabled(
        source_tag="source:corpora",
        title="card 100",
        disabled=True,
    )
    assert service.sample_entries(tag, limit=1)[0].title == "card 099"


def test_sample_entries_excludes_normalized_disabled_title(tmp_path, monkeypatch):
    service = open_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path())
    tag = "dataset:tarot-interpretations"
    store.upsert(_entry("Apollo", "source:corpora", tag))
    store.upsert(_entry("Zeus", "source:corpora", tag))
    service.set_entry_disabled(
        source_tag="source:corpora",
        title="ＡＰＯＬＬＯ",
        disabled=True,
    )
    monkeypatch.setattr(random, "randrange", lambda _population: 0)

    assert service.sample_entries(tag, limit=2) == (
        store.get_entry("source:corpora", "Zeus"),
    )


def test_sample_entries_filters_material_type_before_random_selection(tmp_path):
    service = open_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path())
    tag = "dataset:tarot-interpretations"
    store.upsert(_entry("Knowledge card", "source:chime", tag))
    store.upsert(_entry("Corpus card", "source:corpora", tag))
    store.upsert(_entry("Unknown card", "source:unknown", tag))

    knowledge = service.sample_entries(tag, limit=1, material_type="knowledge")
    corpus = service.sample_entries(tag, limit=1, material_type="corpus")
    all_trusted = service.sample_entries(tag, limit=3)

    assert [entry.title for entry in knowledge] == ["Knowledge card"]
    assert [entry.title for entry in corpus] == ["Corpus card"]
    assert {entry.title for entry in all_trusted} == {
        "Knowledge card",
        "Corpus card",
    }


def _pack(*, pack_id: str, material_type: str, title: str, tags=()):
    return validate_pack(
        {
            "schema_version": 1,
            "pack_id": pack_id,
            "material_type": material_type,
            "source": {"name": pack_id, "homepage": "", "license": "CC0"},
            "entries": [
                {
                    "title": title,
                    "terms": {"alias": [f"{title} alias"], "recognition": []},
                    "tags": list(tags),
                    "summary": f"Meaning of {title}",
                    "content": f"Reference for {title}",
                }
            ],
        }
    )


def test_service_uses_one_database_and_searches_all_material_types(tmp_path):
    service = open_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path())
    store.upsert(_entry("Knowledge fact", "source:chime"))
    store.upsert(_entry("Corpus sample", "source:corpora"))

    assert service.database_path() == tmp_path / "knowledge.db"
    assert service.search("Knowledge fact", limit=1)[0].entry.title == "Knowledge fact"
    assert service.search("Corpus sample", limit=1)[0].entry.title == "Corpus sample"


def test_builtin_knowledge_auto_injects_but_builtin_corpus_does_not(tmp_path):
    service = open_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path())
    store.upsert(_entry("Exact knowledge", "source:chime"))
    store.upsert(_entry("Exact corpus", "source:corpora"))

    assert service.build_conversation_context("Exact knowledge appears").hit_count == 1
    assert service.build_conversation_context("Exact corpus appears").hit_count == 0


def test_latin_direct_match_uses_latin_boundaries_but_allows_cjk_adjacency():
    java = _entry("Java", "source:chime")

    assert _is_direct_material_match("Java 开发", java)
    assert _is_direct_material_match("Java开发", java)
    assert _is_direct_material_match("学习Ｊａｖａ", java)
    assert not _is_direct_material_match("JavaScript", java)
    assert not _is_direct_material_match("myjava2", java)


def test_latin_direct_match_preserves_meaningful_punctuation_and_short_symbols():
    node = _entry("node.js", "source:chime")
    cpp = _entry("C++", "source:chime")

    assert _is_direct_material_match("学习 node.js。", node)
    assert not _is_direct_material_match("学习 nodejs。", node)
    assert _is_direct_material_match("C++", cpp)
    assert not _is_direct_material_match("c", cpp)
    assert not _is_direct_material_match("C++ 开发", cpp)


def test_corpus_short_query_reuses_latin_boundaries_and_keeps_cjk_substrings():
    javascript = _entry("JavaScript 入门", "source:corpora")
    java_cjk = _entry("Java开发入门", "source:corpora")
    chinese = _entry("现在全网都在刷你急了你急了的梗", "source:corpora")

    assert not _is_short_query_embedded_in_term("java", javascript)
    assert _is_short_query_embedded_in_term("java", java_cjk)
    assert _is_short_query_embedded_in_term("你急了", chinese)


def test_accented_latin_direct_match_uses_casefolded_boundaries():
    cafe = _entry("Café", "source:chime")

    assert _is_direct_material_match("CAFÉ教程", cafe)
    assert not _is_direct_material_match("caféteria", cafe)


def test_meme_domain_tag_changes_style_not_routing_permission(tmp_path):
    service = open_knowledge(tmp_path)
    pack = _pack(
        pack_id="meme-domain",
        material_type="knowledge",
        title="Tagged phrase",
        tags=("domain:meme", "type:引用"),
    )
    service.install_pack(pack)

    assert service.build_conversation_context("Tagged phrase").hit_count == 0
    service.set_pack_auto_context("meme-domain", enabled=True)
    context = service.build_conversation_context("Tagged phrase")

    assert context.hit_count == 1
    assert "Knowledge type: 引用" in context.text


def test_corpus_pack_can_participate_in_automatic_conversation(tmp_path):
    service = open_knowledge(tmp_path)
    service.install_pack(
        _pack(pack_id="reply-samples", material_type="corpus", title="Reply sample")
    )

    assert service.search("Reply sample", limit=1)
    assert service.list_packs()[0]["auto_context"] is True
    service.set_pack_auto_context("reply-samples", enabled=False)
    assert service.list_packs()[0]["auto_context"] is False


@pytest.mark.asyncio
async def test_automatic_conversation_uses_corpus_without_magic_words(
    monkeypatch,
    tmp_path,
):
    service = open_knowledge(tmp_path)
    entry = _entry("你这瓜保熟吗", "source:corpora")
    entry = KnowledgeEntry(
        title=entry.title,
        terms=entry.terms,
        tags=entry.tags,
        summary="一条回应参考",
        content="保熟，不熟你提着瓜来找我。",
    )
    calls = []

    async def _asearch(*_args, **kwargs):
        calls.append(kwargs)
        return [
            MaterialKnowledgeHit(
                hit=KnowledgeHit(
                    entry=entry,
                    score=1.0,
                    retrieval_modes=("lexical",),
                    lexical_score=1.0,
                ),
                material_type="corpus",
            )
        ]

    monkeypatch.setattr(service, "asearch", _asearch)
    context = await service.abuild_conversation_context("你这瓜保熟吗？")

    assert len(calls) == 1
    assert calls[0]["load_model"] is False
    assert calls[0]["deadline_monotonic"] is None
    assert context.corpus_hits == 1
    assert context.knowledge_hits == 0
    assert "保熟,不熟你提着瓜来找我" in context.text
    assert "Reference material:" in context.text


@pytest.mark.asyncio
async def test_automatic_conversation_rejects_weak_semantic_corpus(
    monkeypatch,
    tmp_path,
):
    service = open_knowledge(tmp_path)
    entry = _entry("无关语料", "source:corpora")

    async def _asearch(*_args, **_kwargs):
        return [
            MaterialKnowledgeHit(
                hit=KnowledgeHit(
                    entry=entry,
                    score=0.69,
                    retrieval_modes=("semantic",),
                    semantic_score=0.69,
                ),
                material_type="corpus",
            )
        ]

    monkeypatch.setattr(service, "asearch", _asearch)
    context = await service.abuild_conversation_context("你好呀")

    assert context.hit_count == 0
    assert context.text == ""


@pytest.mark.asyncio
async def test_short_natural_corpus_phrase_does_not_require_an_intent_command(
    monkeypatch,
    tmp_path,
):
    service = open_knowledge(tmp_path)
    entry = _entry("现在全网都在刷你急了你急了的梗", "source:corpora")

    async def _asearch(*_args, **_kwargs):
        return [
            MaterialKnowledgeHit(
                hit=KnowledgeHit(
                    entry=entry,
                    score=0.62,
                    retrieval_modes=("lexical", "semantic"),
                    lexical_score=3.0,
                    semantic_score=0.62,
                ),
                material_type="corpus",
            )
        ]

    monkeypatch.setattr(service, "asearch", _asearch)
    context = await service.abuild_conversation_context("你急了")

    assert context.corpus_hits == 1
    assert "Conversation trigger:" in context.text


@pytest.mark.asyncio
async def test_automatic_context_only_records_whole_cards_that_fit(
    monkeypatch,
    tmp_path,
):
    """The closing fence marks where untrusted material ends; truncation must keep it.

    One knowledge hit plus one corpus hit with realistic-length fields already
    exceeds the character budget, so appending the fence before truncating would
    silently cut it off and hand the LLM an unterminated block of pack content.
    """
    service = open_knowledge(tmp_path)

    def _long_entry(title: str, source: str) -> KnowledgeEntry:
        return KnowledgeEntry(
            title=title * 12,
            terms={"alias": (), "recognition": ()},
            tags=(source,),
            summary="释义" * 300,
            content="正文" * 600,
        )

    async def _asearch(*_args, **_kwargs):
        return [
            MaterialKnowledgeHit(
                hit=KnowledgeHit(
                    entry=_long_entry("超长知识词条", "source:corpora"),
                    score=0.9,
                    retrieval_modes=("lexical", "semantic"),
                    lexical_score=3.0,
                    semantic_score=0.9,
                ),
                material_type="knowledge",
            ),
            MaterialKnowledgeHit(
                hit=KnowledgeHit(
                    entry=_long_entry("超长语料词条", "source:corpora"),
                    score=0.9,
                    retrieval_modes=("lexical", "semantic"),
                    lexical_score=3.0,
                    semantic_score=0.9,
                ),
                material_type="corpus",
            ),
        ]

    monkeypatch.setattr(service, "asearch", _asearch)
    context = await service.abuild_conversation_context("超长素材问题")

    assert context.knowledge_hits == 1
    assert context.corpus_hits == 0
    assert len(context.card_identities) == 1
    assert context.card_identities[0].normalized_title.startswith("超长知识词条")
    assert "超长语料词条" not in context.text
    assert len(context.text) <= _AUTOMATIC_CONTEXT_MAX_CHARS
    assert context.text.startswith("======[EPHEMERAL CONVERSATION REFERENCE]======")
    assert context.text.endswith(_AUTOMATIC_CONTEXT_CLOSING_FENCE)


@pytest.mark.asyncio
async def test_automatic_context_excludes_cooling_card_before_quota(monkeypatch, tmp_path):
    service = open_knowledge(tmp_path)
    cooling = _entry("冷却词条", "source:chime")
    fresh = _entry("全新词条", "source:chime")

    async def _asearch(*_args, **_kwargs):
        return [
            MaterialKnowledgeHit(
                hit=KnowledgeHit(
                    entry=entry,
                    score=1.0,
                    retrieval_modes=("lexical",),
                    lexical_score=1.0,
                ),
                material_type="knowledge",
            )
            for entry in (cooling, fresh)
        ]

    monkeypatch.setattr(service, "asearch", _asearch)
    context = await service.abuild_conversation_context(
        "冷却词条和全新词条",
        excluded_identities=frozenset(
            {KnowledgeCardIdentity.from_entry(cooling)}
        ),
    )

    assert context.hit_count == 1
    assert context.entry_title == "全新词条"
    assert context.card_identities == (KnowledgeCardIdentity.from_entry(fresh),)
    assert "冷却词条" not in context.text


@pytest.mark.asyncio
async def test_automatic_conversation_shares_one_search_for_knowledge_and_corpus(
    monkeypatch,
    tmp_path,
):
    service = open_knowledge(tmp_path)
    knowledge_entry = _entry("周三电池", "source:chime", "domain:meme")
    corpus_entry = _entry("猫猫回应", "source:corpora")
    calls = 0

    async def _asearch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        assert _kwargs["reserve_material_type_candidates"] is True
        return [
            MaterialKnowledgeHit(
                hit=KnowledgeHit(
                    entry=knowledge_entry,
                    score=1.0,
                    retrieval_modes=("lexical",),
                    lexical_score=1.0,
                ),
                material_type="knowledge",
            ),
            MaterialKnowledgeHit(
                hit=KnowledgeHit(
                    entry=corpus_entry,
                    score=0.82,
                    retrieval_modes=("semantic",),
                    semantic_score=0.82,
                ),
                material_type="corpus",
            ),
        ]

    monkeypatch.setattr(service, "asearch", _asearch)
    context = await service.abuild_conversation_context("周三电池是什么意思？")

    assert calls == 1
    assert context.hit_count == 2
    assert context.knowledge_hits == 1
    assert context.corpus_hits == 1
    assert "Knowledge term: 周三电池" in context.text
    assert "Conversation trigger: 猫猫回应" in context.text


@pytest.mark.asyncio
async def test_automatic_conversation_reserves_candidates_for_each_material(tmp_path):
    service = open_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path())
    store.upsert_many(
        tuple(
            KnowledgeEntry(
                title=f"shared phrase knowledge {index:02d}",
                terms={"alias": ("shared phrase",), "recognition": ()},
                tags=("source:chime",),
                summary="shared phrase",
                content="shared phrase",
            )
            for index in range(30)
        )
    )
    service.install_pack(
        _pack(
            pack_id="reserved-corpus",
            material_type="corpus",
            title="shared phrase",
        )
    )

    selection = await service.aselect_conversation_materials(
        "shared phrase",
        knowledge_limit=1,
        corpus_limit=1,
    )

    assert len(selection.knowledge) == 1
    assert len(selection.corpus) == 1
    assert selection.corpus[0].hit.entry.title == "shared phrase"


def test_material_type_override_rebuilds_auto_route_without_rewriting_entry(tmp_path):
    service = open_knowledge(tmp_path)
    service.install_pack(
        _pack(pack_id="switchable", material_type="knowledge", title="Switch phrase")
    )
    service.set_pack_auto_context("switchable", enabled=True)
    assert service.build_conversation_context("Switch phrase").hit_count == 1

    service.set_pack_material_type_override("switchable", material_type="corpus")

    assert service.search("Switch phrase", limit=1)
    assert service.build_conversation_context("Switch phrase").hit_count == 0
    assert service.list_packs()[0]["effective_material_type"] == "corpus"


def test_service_ignores_unpublished_legacy_layouts(tmp_path):
    legacy_paths = (
        tmp_path / "public-knowledge" / "knowledge.db",
        tmp_path / "moegirl-knowledge" / "knowledge.db",
        tmp_path / "corpora" / "knowledge.db",
    )
    for path in legacy_paths:
        path.parent.mkdir()
        path.write_bytes(b"unpublished development data")

    service = open_knowledge(tmp_path)

    assert service.database_path() == tmp_path / "knowledge.db"
    assert not service.database_path().exists()
    assert all(path.read_bytes() == b"unpublished development data" for path in legacy_paths)
