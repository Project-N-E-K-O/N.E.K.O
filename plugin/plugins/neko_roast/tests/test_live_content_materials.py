from json import dumps

from plugin.plugins.neko_roast.core.live_content import (
    active_engagement_fallback_topic_candidates,
    idle_hosting_beat_candidates,
)
from plugin.plugins.neko_roast.core.live_content_active_catalog import (
    ACTIVE_ENGAGEMENT_FALLBACK_TOPIC_CANDIDATES,
)
from plugin.plugins.neko_roast.core.live_content_host_catalog import (
    DEFAULT_IDLE_HOSTING_BEAT_CATALOG_PATH,
    IDLE_HOSTING_BEAT_CANDIDATES,
    load_idle_hosting_beat_catalog,
)


def test_live_content_catalog_aggregates_preserve_original_order():
    assert len(ACTIVE_ENGAGEMENT_FALLBACK_TOPIC_CANDIDATES) == 36
    assert [
        item["key"] for item in ACTIVE_ENGAGEMENT_FALLBACK_TOPIC_CANDIDATES[:5]
    ] == [
        "fallback:keyboard-busy",
        "fallback:snack-choice",
        "fallback:serious-cat",
        "fallback:tiny-confession",
        "fallback:today-mood-vote",
    ]
    assert (
        ACTIVE_ENGAGEMENT_FALLBACK_TOPIC_CANDIDATES[-1]["key"]
        == "fallback:air-filter-word"
    )

    assert len(IDLE_HOSTING_BEAT_CANDIDATES) == 31
    assert [item["key"] for item in IDLE_HOSTING_BEAT_CANDIDATES[:5]] == [
        "idle:soft-observation",
        "idle:tiny-choice",
        "idle:light-tease",
        "idle:small-mood",
        "idle:one-word-call",
    ]
    assert IDLE_HOSTING_BEAT_CANDIDATES[-1]["key"] == "idle:soft-lamp-choice"
    assert any(beat["key"] == "idle:blanket-fort" for beat in IDLE_HOSTING_BEAT_CANDIDATES)


def test_active_fallback_materials_return_mutable_copies_without_polluting_catalog():
    first = active_engagement_fallback_topic_candidates()
    assert first
    original_title = ACTIVE_ENGAGEMENT_FALLBACK_TOPIC_CANDIDATES[0]["title"]

    first[0]["title"] = "mutated by caller"
    second = active_engagement_fallback_topic_candidates()

    assert second[0]["title"] == original_title
    assert ACTIVE_ENGAGEMENT_FALLBACK_TOPIC_CANDIDATES[0]["title"] == original_title


def test_idle_hosting_materials_return_mutable_copies_without_polluting_catalog():
    first = idle_hosting_beat_candidates()
    assert first
    original_title = IDLE_HOSTING_BEAT_CANDIDATES[0]["title"]

    first[0]["title"] = "mutated by caller"
    second = idle_hosting_beat_candidates()

    assert second[0]["title"] == original_title
    assert IDLE_HOSTING_BEAT_CANDIDATES[0]["title"] == original_title


def test_idle_hosting_json_catalog_loads_required_fields():
    beats = load_idle_hosting_beat_catalog(DEFAULT_IDLE_HOSTING_BEAT_CATALOG_PATH)

    assert len(beats) == len(IDLE_HOSTING_BEAT_CANDIDATES)
    for beat in beats:
        assert beat["key"].startswith("idle:")
        assert beat["live_column"]
        assert beat["shape"]
        assert beat["fun_axis"]
        assert beat["title"]
        assert beat["hint"]
        assert beat["reply_affordance"]
    assert any(beat.get("meme_query") for beat in beats)
    assert next(beat for beat in beats if beat["key"] == "idle:soft-lamp-choice")["meme_query"] == "班味 松弛感"


def test_idle_hosting_json_loader_ignores_bad_json(tmp_path):
    path = tmp_path / "idle_hosting_beats.json"
    path.write_text("{bad json", encoding="utf-8")

    assert load_idle_hosting_beat_catalog(path) == ()


def test_idle_hosting_json_loader_rejects_malformed_or_duplicate_entries(tmp_path):
    path = tmp_path / "idle_hosting_beats.json"
    path.write_text(
        dumps(
            {
                "beats": [
                    {"key": "idle:missing-required-fields"},
                    {
                        "key": "idle:ok",
                        "live_column": "NEKO test",
                        "shape": "soft_observation",
                        "fun_axis": "mood",
                        "title": "测试冷场素材",
                        "hint": "Use this only in tests.",
                        "reply_affordance": "viewer can answer with one test word",
                    },
                    {
                        "key": "idle:ok",
                        "live_column": "NEKO duplicate",
                        "shape": "soft_observation",
                        "fun_axis": "mood",
                        "title": "重复 key",
                        "hint": "Should be skipped.",
                        "reply_affordance": "viewer can answer with one test word",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    beats = load_idle_hosting_beat_catalog(path)

    assert beats == ()
