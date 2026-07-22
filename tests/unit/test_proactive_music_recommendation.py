"""Tests for proactive-chat music recommendation domain behavior."""

import pytest

from main_logic.proactive_chat import music_recommendation as music


def test_format_music_content_uses_localized_fallbacks():
    rendered = music._format_music_content(
        {
            "success": True,
            "data": [{"name": "夜航星", "artist": "", "album": "专辑甲"}],
        },
        "zh",
    )

    assert "夜航星" in rendered
    assert "专辑甲" in rendered
    assert music.MUSIC_SEARCH_RESULT_TEXTS["zh"]["unknown_artist"] in rendered


def test_format_music_content_rejects_failed_or_empty_results():
    assert music._format_music_content({"success": False}, "zh") == ""
    assert music._format_music_content({"success": True, "data": []}, "zh") == ""


def test_append_music_recommendations_deduplicates_and_respects_limit():
    source_links = [
        {
            "title": "A",
            "artist": "Singer",
            "url": "https://music.test/a",
            "source": "音乐推荐",
        }
    ]
    content = {
        "raw_data": {
            "data": [
                {"name": "A", "artist": "Singer", "url": "https://music.test/a"},
                {"name": "B", "artist": "Singer", "url": "https://music.test/b"},
                {"name": "C", "artist": "Singer", "url": "https://music.test/c"},
            ]
        }
    }

    appended = music._append_music_recommendations(source_links, content, limit=2)

    assert appended == 1
    assert [link["title"] for link in source_links] == ["A", "B"]


def test_select_music_recommendation_skips_and_trims_prior_tracks():
    raw_data = {
        "success": True,
        "data": [
            {"name": "A", "artist": "One", "url": "https://music.test/a"},
            {"name": "B", "artist": "Two", "url": "https://music.test/b"},
        ],
    }
    content = {
        "formatted_content": music._format_music_content(raw_data, "zh"),
        "raw_data": raw_data,
    }

    selection = music._select_music_recommendation(
        content,
        lang="zh",
        source_hash=lambda url, title: url,
        should_skip_source=lambda key: key.endswith("/a"),
        lanlan_name="测试角色",
    )

    assert selection.link["title"] == "B"
    assert selection.topic_key == "https://music.test/b"
    assert [track["name"] for track in selection.content["raw_data"]["data"]] == [
        "B"
    ]
    assert "《A》" not in selection.topic
    assert "《B》" in selection.topic
    assert content["raw_data"] is raw_data


def test_select_music_recommendation_returns_empty_when_all_tracks_suppressed():
    content = {
        "formatted_content": "music",
        "raw_data": {"data": [{"name": "A", "url": "a"}]},
    }

    selection = music._select_music_recommendation(
        content,
        lang="zh",
        source_hash=lambda url, title: url,
        should_skip_source=lambda key: True,
    )

    assert selection == music.MusicRecommendationSelection()


def test_build_music_dynamic_context_combines_fuzzy_and_playing_constraints(
    monkeypatch,
):
    monkeypatch.setattr(music, "PROACTIVE_MUSIC_TAG_INSTRUCTIONS", {"zh": "TAG"})
    monkeypatch.setattr(
        music, "get_proactive_music_failsafe_hint", lambda master, lang: "FUZZY"
    )
    monkeypatch.setattr(
        music, "get_proactive_music_strict_constraint", lambda lang: "STRICT"
    )

    context = music._build_music_dynamic_context(
        selected_music_link={"title": "A"},
        music_content={"raw_data": {"best_match": {"status": "fuzzy"}}},
        is_playing_music=True,
        master_name="博士",
        lang="zh",
    )

    assert context == "TAGFUZZYSTRICT"


def test_build_music_playing_hint_uses_unknown_track_fallback(monkeypatch):
    monkeypatch.setattr(
        music, "get_proactive_music_unknown_track_name", lambda lang: "Unknown"
    )
    monkeypatch.setattr(
        music,
        "get_proactive_music_playing_hint",
        lambda track, master, lang: f"{track}:{master}:{lang}",
    )

    hint = music._build_music_playing_hint(
        is_playing_music=True,
        current_track={"name": ""},
        master_name="博士",
        lang="zh",
    )

    assert hint == "Unknown:博士:zh"


@pytest.mark.asyncio
async def test_fetch_music_with_fallback_retries_without_keyword(monkeypatch):
    calls = []

    async def fake_fetch_music_content(*, keyword, limit):
        calls.append((keyword, limit))
        if keyword:
            return {"success": False, "error": "not found"}
        return {"success": True, "data": [{"name": "Fallback"}]}

    monkeypatch.setattr(music, "fetch_music_content", fake_fetch_music_content)

    result = await music._fetch_music_with_fallback("jazz", lanlan_name="测试角色")

    assert result == {"success": True, "data": [{"name": "Fallback"}]}
    assert calls == [("jazz", 5), ("", 5)]


def test_record_music_played_through_delegates_to_history(monkeypatch):
    calls = []

    def fake_clear(lanlan_name, channel):
        calls.append((lanlan_name, channel))
        return 2

    monkeypatch.setattr(music, "_clear_channel_from_proactive_history", fake_clear)

    assert music._record_music_played_through("测试角色") == 2
    assert calls == [("测试角色", "music")]
