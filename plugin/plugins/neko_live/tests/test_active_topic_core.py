from __future__ import annotations

from collections import deque
import importlib
from json import dumps
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_live.core import (
    active_topic_candidate_picker,
    active_topic_live_thread_source,
    active_topic_material_family,
    active_topic_material_profile,
    active_topic_mentions,
    active_topic_pack,
    active_topic_recent_source,
    active_topic_rules,
    active_topic_sources,
    active_topic_trending_source,
    danmaku_text_rules,
    live_content,
    live_content_active_catalog,
    live_content_host_catalog,
)
from plugin.plugins.neko_live.core.active_topic_selector import ActiveTopicSelector


def test_active_topic_slice_imports_without_later_material_or_content_slices() -> None:
    runtime_api = importlib.import_module(
        "plugin.plugins.neko_live.core.runtime_active_topic_api"
    )

    assert runtime_api.RuntimeActiveTopicApiMixin
    assert active_topic_rules._active_topic_material_profile("pick A or B")


def test_content_catalogs_are_available_through_the_shared_facade() -> None:
    assert live_content.active_engagement_fallback_topic_candidates()
    assert live_content.idle_hosting_beat_candidates()


def test_active_content_order_includes_every_defined_candidate_once() -> None:
    defined_keys = [
        item["key"]
        for item in live_content_active_catalog._ALL_FALLBACK_TOPIC_CANDIDATES
    ]
    ordered_keys = list(live_content_active_catalog._FALLBACK_TOPIC_CANDIDATE_KEYS)

    assert len(defined_keys) == len(set(defined_keys))
    assert len(ordered_keys) == len(set(ordered_keys))
    assert set(ordered_keys) == set(defined_keys)
    actual_keys = [
        item["key"]
        for item in live_content.active_engagement_fallback_topic_candidates()
    ]
    assert actual_keys == ordered_keys


@pytest.mark.parametrize(
    ("key", "expected_family"),
    (
        ("fallback:keyboard-busy", "tease"),
        ("fallback:screen-staring-back", "tease"),
        ("fallback:serious-hosting", "tease"),
        ("fallback:tiny-brave-stance", "room_mood"),
        ("fallback:lightstick-reflection", "room_mood"),
    ),
)
def test_active_catalog_entries_keep_their_thematic_family(
    key: str, expected_family: str
) -> None:
    candidate = live_content_active_catalog._FALLBACK_TOPIC_CANDIDATES_BY_KEY[key]

    assert candidate["family"] == expected_family
    assert (
        active_topic_material_family.host_material_family(candidate)
        == expected_family
    )


def test_host_content_fallback_order_includes_every_defined_beat_once() -> None:
    defined_keys = [
        item["key"] for item in live_content_host_catalog._ALL_IDLE_HOSTING_BEATS
    ]
    ordered_keys = list(live_content_host_catalog._IDLE_HOSTING_BEAT_KEYS)

    assert len(defined_keys) == len(set(defined_keys))
    assert len(ordered_keys) == len(set(ordered_keys))
    assert set(ordered_keys) == set(defined_keys)


def test_partial_invalid_host_catalog_rejects_entire_file(tmp_path: Path) -> None:
    valid = dict(live_content_host_catalog._ALL_IDLE_HOSTING_BEATS[0])
    path = tmp_path / "idle_hosting_beats.json"
    path.write_text(dumps({"beats": [valid, {"key": "incomplete"}]}), encoding="utf-8")

    assert live_content_host_catalog.load_idle_hosting_beat_catalog(path) == ()


def test_duplicate_host_catalog_key_rejects_entire_file(tmp_path: Path) -> None:
    valid = dict(live_content_host_catalog._ALL_IDLE_HOSTING_BEATS[0])
    path = tmp_path / "idle_hosting_beats.json"
    path.write_text(dumps({"beats": [valid, valid]}), encoding="utf-8")

    assert live_content_host_catalog.load_idle_hosting_beat_catalog(path) == ()


def test_truncated_valid_host_catalog_rejects_entire_file(tmp_path: Path) -> None:
    valid = dict(live_content.idle_hosting_beat_candidates()[0])
    path = tmp_path / "idle_hosting_beats.json"
    path.write_text(dumps({"beats": [valid]}), encoding="utf-8")

    assert live_content_host_catalog.load_idle_hosting_beat_catalog(path) == ()


def test_dimension_complete_but_key_incomplete_host_catalog_is_rejected(
    tmp_path: Path,
) -> None:
    full_catalog = live_content_host_catalog.load_idle_hosting_beat_catalog(
        live_content_host_catalog.DEFAULT_IDLE_HOSTING_BEAT_CATALOG_PATH
    )
    required_values = {
        "idle_stage": live_content_host_catalog._REQUIRED_IDLE_HOSTING_STAGES,
        "shape": live_content_host_catalog._REQUIRED_IDLE_HOSTING_SHAPES,
        "fun_axis": live_content_host_catalog._REQUIRED_IDLE_HOSTING_AXES,
    }
    selected: dict[str, dict] = {}
    for field, values in required_values.items():
        for value in values:
            beat = next(item for item in full_catalog if item.get(field) == value)
            selected[beat["key"]] = beat

    assert len(selected) < live_content_host_catalog._MIN_IDLE_HOSTING_CATALOG_SIZE
    path = tmp_path / "idle_hosting_beats.json"
    path.write_text(
        dumps({"beats": list(selected.values())}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert live_content_host_catalog.load_idle_hosting_beat_catalog(path) == ()


@pytest.mark.parametrize("title", ("about", "table", "cable", "stable"))
def test_normal_words_containing_ab_are_not_choice_votes(title: str) -> None:
    material = {"title": title, "fun_axis": "mood"}

    assert active_topic_material_family.host_material_family(material) != "choice_vote"
    assert active_topic_pack.active_topic_pack(material) != "micro_poll"


def test_explicit_material_family_wins_over_title_inference() -> None:
    material = {
        "family": "room_mood",
        "title": "pick one",
        "live_column": "NEKO micro poll",
    }

    assert active_topic_material_family.host_material_family(material) == "room_mood"
    assert active_topic_pack.active_topic_pack(material) == "room_mood"


@pytest.mark.parametrize(
    ("material", "expected_pack"),
    (
        ({"family": "room_mood", "title": "room stance check"}, "room_mood"),
        (
            {"family": "food_drink", "reply_affordance": "share your stance"},
            "food_drink",
        ),
        ({"family": "choice_vote", "fun_axis": "stance"}, "micro_poll"),
    ),
)
def test_explicit_material_family_does_not_drift_to_stance_pack(
    material: dict[str, str], expected_pack: str
) -> None:
    assert active_topic_pack.active_topic_pack(material) == expected_pack


def test_explicit_ab_marker_remains_a_choice_vote() -> None:
    assert (
        active_topic_material_family.host_material_family({"title": "A/B vote"})
        == "choice_vote"
    )


def test_inferred_food_family_does_not_drift_to_stance_pack() -> None:
    material = {
        "title": "late-night drink prompt",
        "reply_affordance": "share your stance",
    }

    assert active_topic_material_family.host_material_family(material) == "food_drink"
    assert active_topic_pack.active_topic_pack(material) == "food_drink"


@pytest.mark.parametrize("title", ("late snack", "warm drink", "饮料", "零食"))
def test_food_markers_use_food_drink_profile_axis(title: str) -> None:
    profile = active_topic_material_profile.active_topic_material_profile(title)

    assert profile["fun_axis"] == "food_drink"


@pytest.mark.parametrize("title", ("keyboard", "screen", "desk", "水杯"))
def test_room_object_markers_keep_object_scene_profile_axis(title: str) -> None:
    profile = active_topic_material_profile.active_topic_material_profile(title)

    assert profile["fun_axis"] == "object_scene"


def test_inferred_food_family_does_not_drift_to_live_column_pack() -> None:
    material = {
        "title": "late-night drink prompt",
        "live_column": "NEKO tiny verdict",
    }

    assert active_topic_material_family.host_material_family(material) == "food_drink"
    assert active_topic_pack.active_topic_pack(material) == "food_drink"


def test_inferred_room_mood_family_does_not_drift_to_live_column_pack() -> None:
    material = {
        "title": "weather mood",
        "live_column": "NEKO micro poll",
    }

    assert active_topic_material_family.host_material_family(material) == "room_mood"
    assert active_topic_pack.active_topic_pack(material) == "room_mood"


@pytest.mark.parametrize(
    ("material", "expected_family"),
    (
        (
            {"fun_axis": "mood", "reply_affordance": "share your stance"},
            "room_mood",
        ),
        ({"fun_axis": "mood", "live_column": "NEKO micro poll"}, "room_mood"),
        ({"family": "mood"}, "mood"),
    ),
)
def test_mood_family_alias_uses_canonical_room_mood_pack(
    material: dict[str, str],
    expected_family: str,
) -> None:
    assert (
        active_topic_material_family.host_material_family(material)
        == expected_family
    )
    assert active_topic_pack.active_topic_pack(material) == "room_mood"


def test_ambiguous_family_still_allows_live_column_pack_override() -> None:
    material = {
        "title": "plain topic",
        "fun_axis": "general",
        "live_column": "NEKO micro poll",
    }

    assert active_topic_material_family.host_material_family(material) == "general"
    assert active_topic_pack.active_topic_pack(material) == "micro_poll"


@pytest.mark.parametrize(
    ("title", "live_column", "expected_pack"),
    (
        ("cup or keyboard", "NEKO micro poll", "micro_poll"),
        ("keyboard looks busy", "NEKO tiny verdict", "neko_verdict"),
    ),
)
def test_weak_inferred_object_scene_allows_live_column_pack_override(
    title: str, live_column: str, expected_pack: str
) -> None:
    material = {"title": title, "live_column": live_column}

    assert active_topic_material_family.host_material_family(material) == "object_scene"
    assert active_topic_pack.active_topic_pack(material) == expected_pack

@pytest.mark.parametrize(
    "text",
    (
        "@Alice @neko👋 what do you think",
        "@Alice @neko?",
        "@Alice @neko✨今天播什么",
        "@Alice @猫猫✨今天播什么",
    ),
)
def test_mention_parsers_share_punctuation_and_symbol_boundaries(text: str) -> None:
    assert active_topic_mentions.is_viewer_to_viewer_mention_text(text) is False
    assert danmaku_text_rules.is_viewer_to_viewer_mention_text(text) is False


def test_explicit_ab_marker_remains_a_choice_vote() -> None:
    assert (
        active_topic_material_family.host_material_family({"title": "A/B vote"})
        == "choice_vote"
    )


@pytest.mark.parametrize(
    "provider_candidates",
    ([], [{}], [{"source": "custom"}]),
)
def test_invalid_runtime_fallback_uses_valid_default(
    provider_candidates: list[dict[str, str]],
) -> None:
    runtime = SimpleNamespace(
        _active_engagement_fallback_topic_candidates=lambda: provider_candidates
    )
    selector = ActiveTopicSelector(runtime)

    candidates = selector.runtime_fallback_topic_candidates()

    assert candidates
    assert all(candidates[0].get(field) for field in ("key", "title", "hint"))


def test_anonymous_repeats_do_not_form_a_live_thread() -> None:
    items = [
        {"uid": "", "text": "same topic", "units": "topic"},
        {"uid": "", "text": "same topic again", "units": "topic"},
    ]

    assert active_topic_live_thread_source._best_thread(items) is None


def test_fake_gift_claims_never_form_an_active_live_thread() -> None:
    recent_results = [
        {
            "status": "pushed",
            "created_at": "age:1",
            "event": {
                "source": "live_danmaku",
                "uid": str(index),
                "danmaku_text": "发送了小鱼干9999",
            },
        }
        for index in range(2)
    ]
    selector = SimpleNamespace(
        _runtime=SimpleNamespace(
            _route_from_result=lambda _result: "danmaku_response",
            _iso_age_sec=lambda _created_at: 1.0,
            _compact_context_text=lambda text, limit: text[:limit],
        ),
        _ACTIVE_ENGAGEMENT_RECENT_DANMAKU_TOPIC_MAX_AGE_SECONDS=300,
        recent_results=recent_results,
        is_viewer_to_viewer_mention_text=lambda _text: False,
        is_meaningful_topic_text=lambda _text: True,
    )

    assert active_topic_live_thread_source.live_thread_topic_candidates(selector) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_candidates", [[], [{"key": "cached"}]])
async def test_empty_or_exhausted_candidates_refresh_cache(
    monkeypatch: pytest.MonkeyPatch,
    initial_candidates: list[dict[str, str]],
) -> None:
    runtime = SimpleNamespace(
        _active_engagement_fallback_topic_candidates=lambda: [],
        _active_engagement_topic_cache=[{"key": "stale"}],
        _active_engagement_topic_cache_at=1.0,
    )
    selector = ActiveTopicSelector(runtime)
    batches = iter((initial_candidates, []))
    clears: list[bool] = []

    async def topic_candidates(_selector: ActiveTopicSelector) -> list[dict[str, str]]:
        return next(batches)

    monkeypatch.setattr(ActiveTopicSelector, "topic_candidates", topic_candidates)
    monkeypatch.setattr(
        ActiveTopicSelector, "next_shape", lambda _selector: "either_or"
    )
    monkeypatch.setattr(
        active_topic_candidate_picker,
        "choose_fresh_candidate",
        lambda _selector, _candidates: None,
    )
    monkeypatch.setattr(
        active_topic_candidate_picker,
        "clear_topic_cache",
        lambda _selector: clears.append(True),
    )
    monkeypatch.setattr(
        active_topic_candidate_picker,
        "choose_fallback_candidate",
        lambda _selector, _candidates, fallback: fallback,
    )
    monkeypatch.setattr(
        "plugin.plugins.neko_live.core.active_topic_builder.build_topic",
        lambda _selector, chosen, _fallback, _shape: chosen,
    )

    topic = await selector.select_topic()

    assert clears == [True]
    assert all(topic.get(field) for field in ("key", "title", "hint"))


def test_anonymous_recent_danmaku_flood_is_rejected() -> None:
    recent_results = [
        {
            "status": "pushed",
            "created_at": "2026-07-10T04:00:00Z",
            "event": {
                "source": "live_danmaku",
                "uid": "",
                "danmaku_text": f"pick topic {index}",
            },
        }
        for index in range(3)
    ]
    runtime = SimpleNamespace(
        _route_from_result=lambda _result: "danmaku_response",
        _iso_age_sec=lambda _created_at: 1.0,
        _compact_context_text=lambda text, limit: text[:limit],
    )
    selector = SimpleNamespace(
        _runtime=runtime,
        _ACTIVE_ENGAGEMENT_RECENT_DANMAKU_TOPIC_MAX_AGE_SECONDS=300,
        _active_engagement_recent_topic_sources=deque(),
        _active_engagement_recent_topic_skip_reason="",
        recent_results=recent_results,
        has_streak=lambda *_args: False,
        is_viewer_to_viewer_mention_text=lambda _text: False,
        is_meaningful_topic_text=lambda _text: True,
        topic_filter_reason=lambda _text: "",
        material_profile=lambda _text: {"fun_axis": "choice"},
    )

    assert active_topic_recent_source.recent_danmaku_topic_candidates(selector) == []
    assert selector._active_engagement_recent_topic_skip_reason == "single_viewer_flood"


@pytest.mark.parametrize(
    "text",
    (
        "@Alice @neko what do you think",
        "@Alice @neko?",
        "@Alice @neko👋",
        "@Alice @neko好可爱",
        "@Alice @nekoかわいい",
        "@Alice @nekoカワイイ",
        "@Alice @nekoちゃん何してるの",
        "@Alice @猫猫，今天播什么",
        "@猫猫今天像小电台",
        "@Alice @猫猫ちゃん",
        "@Alice @猫猫✨今天播什么",
    ),
)
def test_neko_mention_wins_over_an_earlier_viewer_mention(text: str) -> None:
    assert not active_topic_mentions.is_viewer_to_viewer_mention_text(text)


@pytest.mark.parametrize("text", ("@猫猫虫 你看这个", "@猫猫好可爱 你看这个"))
def test_spaced_alias_prefixed_nickname_remains_viewer_to_viewer(text: str) -> None:
    assert active_topic_mentions.is_viewer_to_viewer_mention_text(text)


def test_unspaced_cjk_nickname_starting_with_neko_alias_is_not_misdirected():
    assert active_topic_mentions.is_viewer_to_viewer_mention_text("@猫猫咖啡馆 你看这个")


def test_live_thread_units_preserve_english_word_boundaries():
    units = active_topic_live_thread_source._topic_units("cats are very cute")

    assert "cats" in units
    assert "very" in units
    assert "cute" in units
    assert "catsareverycute" not in units


@pytest.mark.asyncio
async def test_trending_source_preserves_first_skip_reason() -> None:
    async def fetcher(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"videos": [{"title": "ordinary title", "bvid": "BV1"}]}

    selector = SimpleNamespace(
        _active_engagement_topic_cache=[],
        _active_engagement_topic_cache_at=0.0,
        _active_engagement_topic_fetcher=fetcher,
        _active_engagement_recent_topic_skip_reason="viewer_to_viewer_mention",
        _runtime=SimpleNamespace(
            _compact_context_text=lambda text, limit: text[:limit]
        ),
        is_meaningful_topic_text=lambda _text: True,
        material_profile=lambda _text: {},
    )

    assert (
        await active_topic_trending_source.bili_trending_topic_candidates(selector)
        == []
    )
    assert (
        selector._active_engagement_recent_topic_skip_reason
        == "viewer_to_viewer_mention"
    )


@pytest.mark.asyncio
async def test_successful_trending_candidate_clears_rejected_sibling_skip_reason() -> None:
    async def fetcher(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "videos": [
                {"title": "ordinary title", "bvid": "BV1"},
                {"title": "weather mood", "bvid": "BV2"},
            ]
        }

    selector = SimpleNamespace(
        _active_engagement_topic_cache=[],
        _active_engagement_topic_cache_at=0.0,
        _active_engagement_topic_fetcher=fetcher,
        _active_engagement_recent_topic_skip_reason="",
        _runtime=SimpleNamespace(
            _compact_context_text=lambda text, limit: text[:limit]
        ),
        is_meaningful_topic_text=lambda _text: True,
        material_profile=lambda text: {"fun_axis": "mood"}
        if text == "weather mood"
        else {},
    )

    candidates = await active_topic_trending_source.bili_trending_topic_candidates(
        selector
    )

    assert [candidate["key"] for candidate in candidates] == ["bili:BV2"]
    assert selector._active_engagement_recent_topic_skip_reason == ""


@pytest.mark.asyncio
async def test_cached_trending_candidate_preserves_recent_skip_reason() -> None:
    cached = [
        {"source": "bili_trending", "key": "bili:BV2", "title": "weather mood"}
    ]
    selector = SimpleNamespace(
        _active_engagement_topic_cache=cached,
        _active_engagement_topic_cache_at=float("inf"),
        _active_engagement_recent_topic_skip_reason="single_viewer_flood",
    )

    candidates = await active_topic_trending_source.bili_trending_topic_candidates(
        selector
    )

    assert candidates == cached
    assert candidates is not cached
    assert selector._active_engagement_recent_topic_skip_reason == "single_viewer_flood"


@pytest.mark.asyncio
async def test_empty_successful_trending_fetch_is_cached() -> None:
    calls = 0

    async def fetcher(limit: int = 6) -> dict:
        nonlocal calls
        calls += 1
        return {"success": True, "videos": []}

    selector = SimpleNamespace(
        _active_engagement_topic_cache=[],
        _active_engagement_topic_cache_at=0.0,
        _active_engagement_topic_fetcher=fetcher,
        _active_engagement_recent_topic_skip_reason="",
        _runtime=SimpleNamespace(_compact_context_text=lambda text, limit: text[:limit]),
        is_meaningful_topic_text=lambda _text: True,
        material_profile=lambda _text: {},
    )

    assert await active_topic_trending_source.bili_trending_topic_candidates(selector) == []
    assert await active_topic_trending_source.bili_trending_topic_candidates(selector) == []
    assert calls == 1


@pytest.mark.asyncio
async def test_trending_aggregation_preserves_recent_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selector = SimpleNamespace(_active_engagement_recent_topic_skip_reason="")
    trending_candidate = {
        "source": "bili_trending",
        "key": "bili:BV2",
        "title": "weather mood",
    }

    def recent(_selector: object) -> list[dict[str, str]]:
        selector._active_engagement_recent_topic_skip_reason = "single_viewer_flood"
        return []

    async def trending(_selector: object) -> list[dict[str, str]]:
        return [trending_candidate]

    monkeypatch.setattr(
        active_topic_sources, "live_thread_topic_candidates", lambda _: []
    )
    monkeypatch.setattr(
        active_topic_sources, "recent_danmaku_topic_candidates", recent
    )
    monkeypatch.setattr(
        active_topic_sources, "bili_trending_topic_candidates", trending
    )

    candidates = await active_topic_sources.topic_candidates(selector)

    assert candidates == [trending_candidate]
    assert selector._active_engagement_recent_topic_skip_reason == "single_viewer_flood"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing_reason",
    (
        "single_viewer_flood",
        "stale_recent_danmaku",
        "avatar_roast_context",
        "non_output_danmaku",
        "filtered_direct_request",
        "filtered_reaction",
        "filtered_runtime_feedback",
        "viewer_to_viewer_mention",
        "recent_danmaku_source_streak",
        "low_confidence_topic",
    ),
)
async def test_successful_trending_preserves_existing_skip_reason(
    existing_reason: str,
) -> None:
    async def fetcher(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "videos": [
                {"title": "ordinary title", "bvid": "BV1"},
                {"title": "weather mood", "bvid": "BV2"},
            ]
        }

    selector = SimpleNamespace(
        _active_engagement_topic_cache=[],
        _active_engagement_topic_cache_at=0.0,
        _active_engagement_topic_fetcher=fetcher,
        _active_engagement_recent_topic_skip_reason=existing_reason,
        _runtime=SimpleNamespace(
            _compact_context_text=lambda text, limit: text[:limit]
        ),
        is_meaningful_topic_text=lambda _text: True,
        material_profile=lambda text: {"fun_axis": "mood"}
        if text == "weather mood"
        else {},
    )

    candidates = await active_topic_trending_source.bili_trending_topic_candidates(
        selector
    )

    assert [candidate["key"] for candidate in candidates] == ["bili:BV2"]
    assert selector._active_engagement_recent_topic_skip_reason == existing_reason
