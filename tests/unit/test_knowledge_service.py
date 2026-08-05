from __future__ import annotations

from collections import OrderedDict
from types import SimpleNamespace

import pytest

import knowledge.api as public_api
import knowledge.engine.retrieval as retrieval_module
from knowledge.collection_specs import (
    GENERIC_REFERENCE_RESPONSE_POLICY,
    CollectionSpec,
    get_reference_details,
)
from knowledge.engine.models import KnowledgeEntry
from knowledge.engine.retrieval import KnowledgeRetriever, MatchPolicy
from knowledge.engine.source_registry import KnowledgeSource
from knowledge.engine.store import KnowledgeStore
from knowledge.service import KnowledgeService


def _spec(*, automatic: bool = True, restricted: bool = False) -> CollectionSpec:
    return CollectionSpec(
        collection_id="reference",
        storage_directory="reference",
        display_name="Reference",
        priority=5,
        auto_context_enabled=automatic,
        restrict_auto_context_to_registered_sources=restricted,
        sources=(KnowledgeSource("source:fixture", "Fixture", license="CC0-1.0"),),
        match_policy=MatchPolicy(title_min_length=3, alias_min_length=3),
        response_policy=GENERIC_REFERENCE_RESPONSE_POLICY,
    )


def _entry(title: str, *, source: str = "fixture") -> KnowledgeEntry:
    return KnowledgeEntry(
        title=title,
        terms={"alias": [f"{title} alias"], "recognition": []},
        tags=(f"source:{source}", "type:reference"),
        summary=f"Meaning of {title}",
        content=f"Details\n- {title} example",
    )


def _service(tmp_path, *, automatic: bool = True, restricted: bool = False):
    spec = _spec(automatic=automatic, restricted=restricted)
    service = KnowledgeService(tmp_path, collections=(spec,))
    KnowledgeStore(service.database_path("reference")).upsert_many(
        (_entry("known phrase"), _entry("second phrase"))
    )
    return service


def test_public_api_is_small_and_does_not_export_engine_primitives() -> None:
    assert "KnowledgeService" in public_api.__all__
    assert "CollectionSpec" in public_api.__all__
    assert "validate_knowledge_identifier" in public_api.__all__
    assert "validate_pack" in public_api.__all__
    assert "KnowledgeStore" not in public_api.__all__
    assert "MatchPolicy" not in public_api.__all__


def test_knowledge_entry_terms_are_immutable() -> None:
    entry = _entry("immutable phrase")

    with pytest.raises(TypeError):
        entry.terms["alias"] = ("changed",)


def test_service_has_no_implicit_builtin_domains(tmp_path) -> None:
    service = public_api.open_knowledge(tmp_path)

    assert service.list_collections() == ()
    with pytest.raises(ValueError, match="unknown knowledge collection"):
        service.search("missing", "anything")


def test_trusted_collection_search_pagination_and_status(tmp_path) -> None:
    service = _service(tmp_path)

    assert service.search("reference", "known phrase")[0].entry.title == "known phrase"
    assert len(service.search_page("reference", "phrase", limit=1)) == 2
    assert service.list_entries("reference", limit=1)[0].title == "known phrase"
    status = service.get_status("reference")
    assert status["entries"] == 2
    assert status["integrity_ok"] is True
    assert status["sources"] == ({"tag": "source:fixture", "entries": 2},)


def test_search_page_clamps_boundaries_and_returns_one_lookahead(
    monkeypatch,
    tmp_path,
) -> None:
    service = _service(tmp_path)
    calls = []

    class Retriever:
        def search(self, query, **kwargs):
            calls.append((query, kwargs))
            return [SimpleNamespace(entry=index) for index in range(kwargs["limit"])]

    monkeypatch.setattr(service, "_retriever", lambda _collection_id: Retriever())

    page = service.search_page("reference", "query", limit=500, offset=50_000)
    first_page = service.search_page("reference", "query", limit=0, offset=-1)

    assert len(page) == 101
    assert len(first_page) == 2
    assert calls == [
        (
            "query",
            {
                "limit": 1_101,
                "allowed_source_tags": None,
                "include_disabled": False,
            },
        ),
        (
            "query",
            {
                "limit": 2,
                "allowed_source_tags": None,
                "include_disabled": False,
            },
        ),
    ]


def test_list_collections_avoids_full_integrity_check(monkeypatch, tmp_path) -> None:
    service = _service(tmp_path)

    def unexpected_integrity_check(_self):
        raise AssertionError("full integrity check must not run")

    monkeypatch.setattr(KnowledgeStore, "integrity_ok", unexpected_integrity_check)

    status = service.list_collections()[0]
    assert status["status"] == "ready"
    assert status["integrity_ok"] is None


def test_exact_recognition_ranks_above_another_title_substring(tmp_path) -> None:
    service = KnowledgeService(tmp_path, collections=(_spec(),))
    KnowledgeStore(service.database_path("reference")).upsert_many(
        (
            KnowledgeEntry(
                title="different heading",
                terms={"alias": (), "recognition": ("needle",)},
                tags=("source:fixture", "type:reference"),
                summary="Exact recognition",
                content="Details\n- exact recognition",
            ),
            KnowledgeEntry(
                title="needle suffix",
                terms={"alias": (), "recognition": ()},
                tags=("source:fixture", "type:reference"),
                summary="Title substring",
                content="Details\n- title substring",
            ),
        )
    )

    results = service.search("reference", "needle")

    assert [hit.entry.title for hit in results[:2]] == [
        "different heading",
        "needle suffix",
    ]


def test_mention_matcher_cache_is_lru_bounded_across_database_paths(
    monkeypatch,
    tmp_path,
) -> None:
    cache = OrderedDict()
    monkeypatch.setattr(retrieval_module, "_MENTION_MATCHER_CACHE", cache)
    paths = []

    for index in range(retrieval_module._MENTION_MATCHER_CACHE_LIMIT + 1):
        store = KnowledgeStore(tmp_path / f"database-{index}" / "knowledge.db")
        title = f"phrase {index:02d}"
        store.upsert(_entry(title))
        paths.append(store.database_path.resolve())
        hits = KnowledgeRetriever(store).find_mentions(f"mention {title}")
        assert hits[0].entry.title == title

    cached_paths = {path for path, _policy in cache}
    assert len(cache) == retrieval_module._MENTION_MATCHER_CACHE_LIMIT
    assert str(paths[0]) not in cached_paths
    assert {str(path) for path in paths[1:]} == cached_paths


def test_mention_matcher_build_does_not_hold_global_cache_lock(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(retrieval_module, "_MENTION_MATCHER_CACHE", OrderedDict())
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry("known phrase"))
    list_active_entries = store.list_active_entries

    def probe_cache_lock():
        assert retrieval_module._MENTION_MATCHER_CACHE_LOCK.acquire(blocking=False)
        retrieval_module._MENTION_MATCHER_CACHE_LOCK.release()
        return list_active_entries()

    monkeypatch.setattr(store, "list_active_entries", probe_cache_lock)

    hits = KnowledgeRetriever(store).find_mentions("mention known phrase")

    assert hits[0].entry.title == "known phrase"


def test_reference_details_preserve_named_prefix_and_apply_total_budget() -> None:
    entry = SimpleNamespace(content="Heading\nFact: abcdef\nFact: ghijkl")

    details = get_reference_details(entry, ("Fact: ",), max_chars=10)

    assert details == "Fact: abcd"
    assert len(details) == 10


def test_disable_and_restore_affects_search_and_routing(tmp_path) -> None:
    service = _service(tmp_path)
    assert service.build_turn_context("known phrase appears").hit_count == 1

    service.set_entry_disabled(
        "reference",
        source_tag="source:fixture",
        title="known phrase",
        disabled=True,
    )
    assert service.search("reference", "known phrase") == []
    assert service.build_turn_context("known phrase appears").hit_count == 0

    service.set_entry_disabled(
        "reference",
        source_tag="source:fixture",
        title="known phrase",
        disabled=False,
    )
    assert service.build_turn_context("known phrase appears").hit_count == 1


def test_collection_override_only_changes_automatic_context(tmp_path) -> None:
    service = _service(tmp_path)
    service.set_collection_auto_context("reference", enabled=False)

    assert service.build_turn_context("known phrase appears").hit_count == 0
    assert service.search("reference", "known phrase")

    restarted = KnowledgeService(tmp_path, collections=(_spec(),))
    assert restarted.build_turn_context("known phrase appears").hit_count == 0


def test_registered_source_restriction_excludes_untrusted_source_from_context(tmp_path) -> None:
    spec = _spec(restricted=True)
    service = KnowledgeService(tmp_path, collections=(spec,))
    store = KnowledgeStore(service.database_path("reference"))
    store.upsert(_entry("trusted phrase"))
    store.upsert(_entry("other phrase", source="other"))

    assert service.build_turn_context("trusted phrase appears").hit_count == 1
    assert service.build_turn_context("other phrase appears").hit_count == 0
    assert service.search("reference", "other phrase")


def test_context_card_uses_collection_source_metadata(tmp_path) -> None:
    service = _service(tmp_path)

    context = service.build_turn_context("known phrase appears")

    assert context.hit_count == 1
    assert "Meaning of known phrase" in context.text
    assert "Source: Fixture" in context.text
    assert "CC0" not in context.text
