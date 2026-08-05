from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import pytest

from knowledge.builtin import open_builtin_knowledge
from knowledge.corpora import (
    CORPORA_COMMIT,
    CORPORA_ENTRY_COUNT,
    CORPORA_LICENSE,
    CORPORA_SHA256,
    import_bundled_corpora,
    load_bundled_corpora_dataset,
)
from knowledge.engine.models import KnowledgeEntry
from knowledge.engine.store import KnowledgeStore


def test_bundled_corpora_asset_is_small_valid_pinned_jsonl() -> None:
    dataset = load_bundled_corpora_dataset()
    lines = (
        files("knowledge.data")
        .joinpath("corpora_demo.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert len(dataset.entries) == len(lines) == CORPORA_ENTRY_COUNT == 229
    assert dataset.commit == CORPORA_COMMIT
    assert dataset.sha256 == CORPORA_SHA256
    assert CORPORA_LICENSE == "CC0 1.0"
    assert all(line and isinstance(json.loads(line), dict) for line in lines)
    assert {entry.source_tag for entry in dataset.entries} == {"source:corpora"}
    assert len({entry.title.casefold() for entry in dataset.entries}) == 229
    assert {
        tag
        for entry in dataset.entries
        for tag in entry.tags
        if tag.startswith("category:")
    } == {
        "category:animals",
        "category:colors",
        "category:divination",
        "category:film-tv",
        "category:foods",
        "category:humans",
        "category:mythology",
        "category:psychology",
    }


def test_import_is_idempotent_and_preserves_other_sources(tmp_path: Path) -> None:
    first = import_bundled_corpora(tmp_path)
    service = open_builtin_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path("corpora"))
    store.upsert(
        KnowledgeEntry(
            title="Local Reference",
            terms={},
            tags=("source:community.local",),
            summary="Local summary",
            content="Local content",
        )
    )
    store.upsert(
        KnowledgeEntry(
            title="Aphrodite",
            terms={},
            tags=("source:corpora", "category:mythology"),
            summary="Damaged summary",
            content="Damaged content",
        )
    )
    repaired = import_bundled_corpora(tmp_path)
    repaired_revision = store.entries_revision()
    second = import_bundled_corpora(tmp_path)

    assert first.changed is True
    assert repaired.changed is True
    assert second.changed is False
    assert first.entries == second.entries == 229
    assert store.entries_revision() == repaired_revision
    assert service.count_entries("corpora", source_tag="source:corpora") == 229
    assert store.get_entry("source:community.local", "Local Reference") is not None
    assert store.get_entry("source:corpora", "Aphrodite").summary != "Damaged summary"  # type: ignore[union-attr]


def test_corpora_search_management_routing_and_sampling(tmp_path: Path) -> None:
    import_bundled_corpora(tmp_path)
    service = open_builtin_knowledge(tmp_path)

    assert service.search("corpora", "Aphrodite", limit=1)[0].entry.title == "Aphrodite"
    assert service.search("corpora", "The Godfather", limit=1)[0].entry.title == (
        "The Godfather (1972)"
    )
    assert service.search("corpora", "#0000FF", limit=1)[0].entry.title == "Blue"
    assert service.build_turn_context("Aphrodite is a mythic name").collection_id == (
        "corpora"
    )
    moon = service.build_turn_context("I drew The Moon today")
    assert moon.collection_id == "corpora"
    assert "Light meanings:" in moon.text
    assert "only as entertainment and symbolic reflection" in moon.text
    assert "Never present it as health, legal, or financial advice" in moon.text
    assert service.build_turn_context("my cat is calm").hit_count == 0

    tarot = service.build_conversation_context("给我抽一张塔罗牌")
    occupation = service.build_conversation_context("给我一个NPC职业")
    assert tarot.match_mode == occupation.match_mode == "material_sample"
    assert tarot.collection_id == occupation.collection_id == "corpora"
    assert service.build_conversation_context("我们在讨论职业教育").hit_count == 0

    sampled = service.sample_entries("corpora", "dataset:occupations", limit=2)
    assert len(sampled) == 2
    assert all("dataset:occupations" in entry.tags for entry in sampled)
    with pytest.raises(ValueError, match="sample tag is not enabled"):
        service.sample_entries("corpora", "category:humans")

    indigo = service.search("corpora", "#4B0082", limit=1)[0].entry
    assert indigo.title == "Indigo"
    assert "Indigo has hexadecimal" in indigo.summary

    assert service.set_entry_disabled(
        "corpora",
        source_tag="source:corpora",
        title="Aphrodite",
        disabled=True,
    ) == 1
    assert service.search("corpora", "Aphrodite") == []


def test_corpora_source_license_is_collection_scoped(tmp_path: Path) -> None:
    import_bundled_corpora(tmp_path)
    service = open_builtin_knowledge(tmp_path)
    context = service.build_turn_context("Aphrodite is a mythic name")

    assert "Source: Darius Kazemi's Corpora" in context.text
    assert "license" not in context.text.casefold()
    license_text = files("knowledge.data").joinpath("LICENSE-CORPORA.txt").read_text(
        encoding="utf-8"
    )
    assert "CC0 1.0" in license_text
