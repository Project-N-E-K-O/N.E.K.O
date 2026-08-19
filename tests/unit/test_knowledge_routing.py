from __future__ import annotations

from contextlib import contextmanager
import logging
import threading

import pytest

import knowledge.engine.routing as routing_module
from knowledge.collection_specs import (
    GENERIC_REFERENCE_RESPONSE_POLICY,
    CollectionSpec,
)
from knowledge.engine.models import KnowledgeEntry
from knowledge.engine.retrieval import MatchPolicy
from knowledge.engine.routing import ContextHint
from knowledge.engine.store import KnowledgeStore
from knowledge.service import KnowledgeService


def _spec(
    collection_id: str,
    *,
    priority: int = 0,
    automatic: bool = True,
    context_hints: tuple[ContextHint, ...] = (),
):
    return CollectionSpec(
        collection_id=collection_id,
        storage_directory=collection_id,
        priority=priority,
        auto_context_enabled=automatic,
        match_policy=MatchPolicy(),
        response_policy=GENERIC_REFERENCE_RESPONSE_POLICY,
        context_hints=context_hints,
    )


def _entry(title: str, *, source: str, recognition=(), tags=()):
    return KnowledgeEntry(
        title=title,
        terms={"alias": (), "recognition": recognition},
        tags=(f"source:{source}", "type:reference", *tags),
        summary=f"Meaning of {title}",
        content=f"Meaning\n- {title} used in context",
    )


def _service_with_entries(tmp_path, specs, rows):
    service = KnowledgeService(tmp_path, collections=specs)
    for collection_id, entries in rows.items():
        KnowledgeStore(service.database_path(collection_id)).upsert_many(tuple(entries))
    return service


def test_warm_no_hit_and_hot_hit_do_not_open_any_database(monkeypatch, tmp_path):
    specs = (_spec("first"), _spec("second", priority=10))
    service = _service_with_entries(
        tmp_path,
        specs,
        {
            "first": (_entry("first phrase", source="first"),),
            "second": (_entry("second phrase", source="second"),),
        },
    )
    service.refresh_routing_index()
    original = KnowledgeStore._connection
    opened = 0

    @contextmanager
    def counted(self, *, writable=False):
        nonlocal opened
        opened += 1
        with original(self, writable=writable) as connection:
            yield connection

    monkeypatch.setattr(KnowledgeStore, "_connection", counted)

    assert service.build_turn_context("nothing relevant here").hit_count == 0
    assert opened == 0
    first = service.build_turn_context("using second phrase now")
    assert first.hit_count == 1
    assert opened == 1
    opened = 0
    second = service.build_turn_context("using second phrase again")
    assert second.hit_count == 1
    assert opened == 0


def test_global_route_selects_one_high_priority_card_across_five_collections(tmp_path):
    specs = tuple(_spec(f"collection-{index}", priority=index) for index in range(5))
    service = _service_with_entries(
        tmp_path,
        specs,
        {
            spec.collection_id: (
                _entry("shared phrase", source=spec.collection_id),
            )
            for spec in specs
        },
    )

    context = service.build_turn_context("shared phrase appears")

    assert context.hit_count == 1
    assert context.collection_id == "collection-4"
    assert context.text.count("Term: shared phrase") == 1


def test_context_hint_disambiguates_equal_cross_collection_matches(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="knowledge.engine.routing")
    reference = _spec(
        "reference",
        priority=100,
        context_hints=(ContextHint(terms=("reference meaning",)),),
    )
    tarot = _spec(
        "tarot",
        priority=10,
        context_hints=(ContextHint(
            required_tags=("dataset:tarot",),
            terms=("抽到", "这张牌"),
        ),),
    )
    service = _service_with_entries(
        tmp_path,
        (reference, tarot),
        {
            "reference": (_entry("The Moon", source="reference"),),
            "tarot": (_entry(
                "The Moon",
                source="tarot",
                tags=("dataset:tarot",),
            ),),
        },
    )

    tarot_context = service.build_turn_context("我抽到了 The Moon，这张牌怎么解释？")
    reference_context = service.build_turn_context("The Moon reference meaning")

    assert tarot_context.collection_id == "tarot"
    assert reference_context.collection_id == "reference"
    assert tarot_context.hit_count == reference_context.hit_count == 1
    assert any(
        "resolution=context_hint collection=tarot" in message
        for message in caplog.messages
    )


def test_context_hint_cannot_trigger_an_unmentioned_entry(tmp_path):
    service = _service_with_entries(
        tmp_path,
        (_spec(
            "tarot",
            context_hints=(ContextHint(terms=("抽到", "这张牌")),),
        ),),
        {"tarot": (_entry("The Moon", source="tarot"),)},
    )

    assert service.build_turn_context("我抽到这张牌了").hit_count == 0


def test_context_hint_does_not_override_a_better_match_mode(tmp_path):
    weak_policy = MatchPolicy(
        title_min_length=3,
        weak_term_length=2,
        weak_required_tags=("source:weak",),
        weak_required_tag_prefixes=("type:",),
        weak_content_line_prefix="- ",
    )
    weak = CollectionSpec(
        collection_id="weak",
        storage_directory="weak",
        priority=100,
        auto_context_enabled=True,
        match_policy=weak_policy,
        response_policy=GENERIC_REFERENCE_RESPONSE_POLICY,
        context_hints=(ContextHint(terms=("strong context",)),),
    )
    strong = _spec("strong", priority=1)
    service = _service_with_entries(
        tmp_path,
        (weak, strong),
        {
            "weak": (_entry("xy", source="weak"),),
            "strong": (_entry("confirmed phrase", source="strong"),),
        },
    )

    context = service.build_turn_context("xy confirmed phrase strong context")

    assert context.collection_id == "strong"
    assert context.match_mode == "strong"


def test_latin_context_hint_uses_word_boundaries(tmp_path):
    high = _spec("high", priority=100)
    low = _spec(
        "low",
        priority=1,
        context_hints=(ContextHint(terms=("tarot",)),),
    )
    service = _service_with_entries(
        tmp_path,
        (high, low),
        {
            "high": (_entry("shared phrase", source="high"),),
            "low": (_entry("shared phrase", source="low"),),
        },
    )

    assert service.build_turn_context(
        "tarot shared phrase"
    ).collection_id == "low"
    assert service.build_turn_context(
        "tarotology shared phrase"
    ).collection_id == "high"


def test_mixed_language_latin_route_preserves_complete_phrase(tmp_path):
    spec = CollectionSpec(
        collection_id="mixed",
        storage_directory="mixed",
        auto_context_enabled=True,
        match_policy=MatchPolicy(
            title_min_length=3,
            alias_min_length=3,
            latin_word_boundaries=True,
        ),
        response_policy=GENERIC_REFERENCE_RESPONSE_POLICY,
    )
    service = _service_with_entries(
        tmp_path,
        (spec,),
        {"mixed": (_entry("猫娘 ab", source="mixed"),)},
    )
    service.refresh_routing_index()

    assert service.build_turn_context("猫娘 ab 是什么").hit_count == 1
    assert service.build_turn_context("only ab appears").hit_count == 0


def test_latin_boundaries_do_not_split_mixed_or_non_ascii_phrases(tmp_path):
    spec = CollectionSpec(
        collection_id="mixed-boundaries",
        storage_directory="mixed-boundaries",
        auto_context_enabled=True,
        match_policy=MatchPolicy(
            title_min_length=3,
            alias_min_length=3,
            latin_word_boundaries=True,
        ),
        response_policy=GENERIC_REFERENCE_RESPONSE_POLICY,
    )
    service = _service_with_entries(
        tmp_path,
        (spec,),
        {
            "mixed-boundaries": (
                _entry("猫 cafe", source="mixed-boundaries"),
                _entry("café", source="mixed-boundaries"),
                _entry("asciiword", source="mixed-boundaries"),
                _entry("123", source="mixed-boundaries"),
            ),
        },
    )

    assert service.build_turn_context("我想了解猫 cafe").hit_count == 1
    assert service.build_turn_context("only cafe appears").hit_count == 0
    assert service.build_turn_context("tell me about café").hit_count == 1
    assert service.build_turn_context("plain cafe").hit_count == 0
    assert service.build_turn_context("asciiword").hit_count == 1
    assert service.build_turn_context("asciiwording").hit_count == 0
    assert service.build_turn_context("number 123").hit_count == 1
    assert service.build_turn_context("number 1234").hit_count == 0


def test_equal_context_hints_fall_back_to_collection_priority(tmp_path):
    high = _spec(
        "high",
        priority=100,
        context_hints=(ContextHint(terms=("first clue",)),),
    )
    low = _spec(
        "low",
        priority=1,
        context_hints=(ContextHint(terms=("other clue",)),),
    )
    service = _service_with_entries(
        tmp_path,
        (high, low),
        {
            "high": (_entry("shared phrase", source="high"),),
            "low": (_entry("shared phrase", source="low"),),
        },
    )

    context = service.build_turn_context(
        "first clue and other clue both describe shared phrase"
    )

    assert context.collection_id == "high"


def test_context_hint_does_not_override_a_longer_strong_match(tmp_path):
    hinted = _spec(
        "hinted",
        priority=100,
        context_hints=(ContextHint(terms=("tarot",)),),
    )
    specific = _spec("specific", priority=1)
    service = _service_with_entries(
        tmp_path,
        (hinted, specific),
        {
            "hinted": (_entry("moon card", source="hinted"),),
            "specific": (_entry("The Moon tarot card", source="specific"),),
        },
    )

    context = service.build_turn_context("tarot: The Moon tarot card")

    assert context.collection_id == "specific"


def test_strong_route_beats_a_higher_priority_weak_route(tmp_path):
    weak_policy = MatchPolicy(
        title_min_length=3,
        weak_term_length=2,
        weak_required_tags=("source:weak",),
        weak_required_tag_prefixes=("type:",),
        weak_content_line_prefix="- ",
    )
    weak = CollectionSpec(
        collection_id="weak",
        storage_directory="weak",
        priority=100,
        auto_context_enabled=True,
        match_policy=weak_policy,
        response_policy=GENERIC_REFERENCE_RESPONSE_POLICY,
    )
    strong = _spec("strong", priority=1)
    service = _service_with_entries(
        tmp_path,
        (weak, strong),
        {
            "weak": (_entry("xy", source="weak"),),
            "strong": (_entry("confirmed phrase", source="strong"),),
        },
    )

    context = service.build_turn_context("xy and confirmed phrase")

    assert context.collection_id == "strong"
    assert context.match_mode == "strong"


def test_only_the_changed_collection_route_segment_is_reloaded(monkeypatch, tmp_path):
    specs = (_spec("first"), _spec("second"))
    service = _service_with_entries(
        tmp_path,
        specs,
        {
            "first": (_entry("first phrase", source="first"),),
            "second": (_entry("second phrase", source="second"),),
        },
    )
    original = KnowledgeStore.load_routing_entries
    loaded: list[str] = []

    def counted(self):
        loaded.append(self.database_path.parent.name)
        return original(self)

    monkeypatch.setattr(KnowledgeStore, "load_routing_entries", counted)
    service.refresh_routing_index()
    assert sorted(loaded) == ["first", "second"]
    loaded.clear()

    KnowledgeStore(service.database_path("second")).upsert(
        _entry("new second phrase", source="second")
    )
    service.refresh_routing_index()

    assert loaded == ["second"]
    assert service.build_turn_context("new second phrase").collection_id == "second"


def test_route_records_do_not_retain_summary_or_content(tmp_path):
    service = _service_with_entries(
        tmp_path,
        (_spec("reference"),),
        {"reference": (_entry("compact route", source="reference"),)},
    )
    service.refresh_routing_index()
    state = service._get_routing_state()
    record = state._segments["reference"][0]

    assert not hasattr(record, "summary")
    assert not hasattr(record, "content")
    assert record.strong_terms == ("compactroute",)


def test_card_cache_is_bounded_to_256_entries(tmp_path):
    service = _service_with_entries(
        tmp_path,
        (_spec("reference"),),
        {
            "reference": tuple(
                _entry(f"cache phrase {index:03d}", source="reference")
                for index in range(257)
            )
        },
    )
    service.refresh_routing_index()

    for index in range(257):
        assert service.build_turn_context(f"cache phrase {index:03d}").hit_count == 1

    assert service._get_routing_state().cache_size() == 256


def test_a_corrupt_collection_does_not_block_another_collection(tmp_path):
    specs = (_spec("broken", priority=100), _spec("healthy"))
    service = KnowledgeService(tmp_path, collections=specs)
    broken_path = service.database_path("broken")
    broken_path.parent.mkdir(parents=True)
    broken_path.write_bytes(b"not a sqlite database")
    KnowledgeStore(service.database_path("healthy")).upsert(
        _entry("healthy phrase", source="healthy")
    )

    service.refresh_routing_index()
    context = service.build_turn_context("healthy phrase appears")

    assert context.hit_count == 1
    assert context.collection_id == "healthy"


def test_an_unexpected_segment_failure_is_isolated(monkeypatch, tmp_path):
    specs = (_spec("broken"), _spec("healthy"))
    service = _service_with_entries(
        tmp_path,
        specs,
        {
            "broken": (_entry("broken phrase", source="broken"),),
            "healthy": (_entry("healthy phrase", source="healthy"),),
        },
    )
    original = KnowledgeStore.load_routing_entries

    def failing(self):
        if self.database_path.parent.name == "broken":
            raise RuntimeError("fixture failure")
        return original(self)

    monkeypatch.setattr(KnowledgeStore, "load_routing_entries", failing)

    service.refresh_routing_index()

    assert service.build_turn_context("broken phrase").hit_count == 0
    assert service.build_turn_context("healthy phrase").collection_id == "healthy"


def test_unknown_explicit_collection_is_rejected(tmp_path):
    service = KnowledgeService(tmp_path, collections=(_spec("known"),))

    with pytest.raises(ValueError, match="unknown knowledge collection"):
        service.build_turn_context("anything", collection_ids=("unknown",))


def test_background_refresh_keeps_the_previous_snapshot_available(monkeypatch, tmp_path):
    service = _service_with_entries(
        tmp_path,
        (_spec("reference"),),
        {"reference": (_entry("existing phrase", source="reference"),)},
    )
    service.refresh_routing_index()
    assert service.build_turn_context("existing phrase").hit_count == 1
    original = KnowledgeStore.load_routing_entries
    refresh_started = threading.Event()
    release_refresh = threading.Event()

    def delayed(self):
        refresh_started.set()
        assert release_refresh.wait(timeout=3)
        return original(self)

    monkeypatch.setattr(KnowledgeStore, "load_routing_entries", delayed)
    KnowledgeStore(service.database_path("reference")).upsert(
        _entry("new phrase", source="reference")
    )
    service.refresh_routing_index(background=True)
    assert refresh_started.wait(timeout=1)
    completed = threading.Event()

    def read_old_snapshot():
        assert service.build_turn_context("existing phrase").hit_count == 1
        completed.set()

    reader = threading.Thread(target=read_old_snapshot)
    reader.start()
    assert completed.wait(timeout=3)
    state = service._get_routing_state()
    refresh_thread = state._refresh_thread
    assert refresh_thread is not None
    release_refresh.set()
    reader.join(timeout=1)
    refresh_thread.join(timeout=3)
    assert not reader.is_alive()
    assert not refresh_thread.is_alive()
    assert state._refresh_thread is None
    assert service.build_turn_context("new phrase").hit_count == 1


def test_evicted_routing_state_still_receives_database_notifications(tmp_path):
    services = []
    for index in range(routing_module._STATE_CACHE_LIMIT + 1):
        collection_id = f"live-{index}"
        service = _service_with_entries(
            tmp_path,
            (_spec(collection_id),),
            {collection_id: (_entry("old phrase", source=collection_id),)},
        )
        service.refresh_routing_index()
        services.append(service)

    first = services[0]
    first_state = first._get_routing_state()
    assert first_state not in routing_module._STATES.values()

    KnowledgeStore(first.database_path("live-0")).upsert(
        _entry("new phrase", source="live-0")
    )

    assert first.build_turn_context("new phrase").hit_count == 1


def test_refresh_is_bounded_and_leaves_concurrent_changes_dirty(monkeypatch, tmp_path):
    service = _service_with_entries(
        tmp_path,
        (_spec("bounded"),),
        {"bounded": (_entry("old phrase", source="bounded"),)},
    )
    service.refresh_routing_index()
    state = service._get_routing_state()
    database_path = service.database_path("bounded")
    calls = 0
    keep_changing = True

    def changing_segment(collection):
        nonlocal calls
        calls += 1
        if keep_changing:
            state.mark_database_dirty(collection.database_path)
        return ()

    monkeypatch.setattr(routing_module, "_safe_load_segment", changing_segment)
    state.mark_database_dirty(database_path)
    state.refresh()

    assert calls == routing_module._MAX_REFRESH_ROUNDS
    assert state._dirty == {"bounded"}

    keep_changing = False
    state.refresh()
    assert state._dirty == set()


def test_background_refresh_logs_only_safe_exception_metadata(
    monkeypatch,
    tmp_path,
    caplog,
):
    service = _service_with_entries(
        tmp_path,
        (_spec("background-error"),),
        {"background-error": (_entry("old phrase", source="background-error"),)},
    )
    state = service._get_routing_state()
    started = threading.Event()
    release = threading.Event()

    def failing_refresh():
        started.set()
        assert release.wait(timeout=3)
        raise RuntimeError("private entry content")

    monkeypatch.setattr(state, "refresh", failing_refresh)
    caplog.set_level(logging.WARNING, logger="knowledge.engine.routing")
    state.refresh_in_background()
    assert started.wait(timeout=1)
    refresh_thread = state._refresh_thread
    assert refresh_thread is not None
    release.set()
    refresh_thread.join(timeout=3)

    assert not refresh_thread.is_alive()
    assert state._refresh_thread is None
    assert "type=RuntimeError" in caplog.text
    assert "private entry content" not in caplog.text


def test_database_schema_initialization_runs_once_per_file(monkeypatch, tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    original = KnowledgeStore._initialize
    calls = 0

    def counted(self, connection):
        nonlocal calls
        calls += 1
        return original(self, connection)

    monkeypatch.setattr(KnowledgeStore, "_initialize", counted)

    assert store.count() == 0
    assert store.count() == 0
    assert store.list_entries() == ()
    assert calls == 1


def test_replaced_database_file_is_initialized_again(tmp_path):
    database_path = tmp_path / "knowledge.db"
    store = KnowledgeStore(database_path)
    store.upsert(_entry("old phrase", source="fixture"))
    database_path.unlink()

    replacement = KnowledgeStore(database_path)

    assert replacement.count() == 0
    assert replacement.integrity_ok()


def test_user_text_is_normalized_once_across_five_collections(tmp_path):
    from knowledge.engine.filters import normalize_search_text

    calls = 0

    def counted(value):
        nonlocal calls
        calls += 1
        return normalize_search_text(value)

    specs = tuple(
        CollectionSpec(
            collection_id=f"collection-{index}",
            storage_directory=f"collection-{index}",
            auto_context_enabled=True,
            match_policy=MatchPolicy(normalizer=counted),
            response_policy=GENERIC_REFERENCE_RESPONSE_POLICY,
        )
        for index in range(5)
    )
    service = _service_with_entries(
        tmp_path,
        specs,
        {
            spec.collection_id: (
                _entry(f"phrase {index}", source=spec.collection_id),
            )
            for index, spec in enumerate(specs)
        },
    )
    service.refresh_routing_index()
    calls = 0

    assert service.build_turn_context("ordinary unmatched text").hit_count == 0
    assert calls == 1
