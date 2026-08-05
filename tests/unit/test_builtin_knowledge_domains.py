from __future__ import annotations

from knowledge.api import open_knowledge
from knowledge.builtin import BUILTIN_COLLECTIONS, open_builtin_knowledge
from knowledge.corpora import import_bundled_corpora
from knowledge.engine.models import KnowledgeEntry
from knowledge.engine.store import KnowledgeStore
from knowledge.moegirl_knowledge import (
    MEME_COLLECTION,
    normalize_meme_phrase,
)


def _meme_entry(
    title: str,
    *,
    entry_type: str = "现象",
    extra_tags: tuple[str, ...] = (),
) -> KnowledgeEntry:
    return KnowledgeEntry(
        title=title,
        terms={"alias": [], "recognition": []},
        tags=("source:chime", f"type:{entry_type}", *extra_tags),
        summary=f"Meaning of {title}",
        content=f"Meaning\n- {title} used in context",
    )


def test_generic_api_stays_empty_while_builtin_composition_is_explicit(tmp_path) -> None:
    assert open_knowledge(tmp_path).list_collections() == ()

    service = open_builtin_knowledge(tmp_path)

    assert tuple(spec.collection_id for spec in BUILTIN_COLLECTIONS) == (
        "meme",
        "corpora",
    )
    assert {row["collection_id"] for row in service.list_collections()} == {
        "meme",
        "corpora",
    }


def test_meme_policy_owns_phrase_normalization_and_response_direction(tmp_path) -> None:
    assert normalize_meme_phrase("他在 CPU 你") == "人在cpu人"
    assert normalize_meme_phrase("他这是在 CPU 我吧？") == "人在cpu人"
    service = open_builtin_knowledge(tmp_path)
    KnowledgeStore(service.database_path("meme")).upsert(
        _meme_entry("他在 CPU 你")
    )

    context = service.build_turn_context("你这是在 CPU 我吗？")

    assert context.collection_id == "meme"
    assert context.match_mode == "strong"
    assert "EPHEMERAL MEME RESPONSE TASK" in context.text
    assert "Do not treat self-mockery" in context.text
    assert "Meme type: 现象" in context.text
    assert "Source: CHIME" in context.text


def test_meme_short_hint_and_stale_usage_boundaries(tmp_path) -> None:
    service = open_builtin_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path("meme"))
    store.upsert(_meme_entry("上头"))
    store.upsert(
        _meme_entry("水灵灵", extra_tags=("quality:stale-usage",))
    )

    short = service.build_turn_context("这个方案越改越上头")

    assert short.match_mode == "weak_short"
    assert "POSSIBLE SHORT MEME TASK" in short.text
    assert service.build_turn_context("她水灵灵地上线了").hit_count == 0


def test_equal_title_is_disambiguated_by_domain_context(tmp_path) -> None:
    import_bundled_corpora(tmp_path)
    service = open_builtin_knowledge(tmp_path)
    KnowledgeStore(service.database_path("meme")).upsert(
        _meme_entry("The Moon", entry_type="引用")
    )

    tarot = service.build_turn_context("我抽到了 The Moon，这张牌是什么意思？")
    meme = service.build_turn_context("The Moon 是什么梗？")
    bare = service.build_turn_context("The Moon")

    assert tarot.collection_id == "corpora"
    assert meme.collection_id == "meme"
    assert bare.collection_id == "meme"


def test_meme_domain_does_not_bundle_or_fetch_a_dataset(tmp_path) -> None:
    assert MEME_COLLECTION.sources
    assert not (tmp_path / "moegirl-knowledge").exists()

    service = open_builtin_knowledge(tmp_path)

    assert service.count_entries("meme") == 0
    assert not (tmp_path / "moegirl-knowledge").exists()
