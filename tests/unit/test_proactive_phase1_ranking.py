from unittest.mock import AsyncMock

import pytest

from config.prompts.prompts_proactive import build_unified_phase1_prompt
from main_logic.proactive_chat import candidate_selection, service, sources, state
from main_logic.proactive_chat.generation import (
    _fetch_phase1_followups,
    _lookup_link_by_phase1_selection,
)
from main_logic.proactive_chat.interest_retrieval import public_interest_topics
from main_logic.proactive_chat.phase1_ranking import history_aliases
from utils.proactive_preferences.personalization import LocalPreferences
from utils.proactive_preferences.preference_model import Profile
from utils.proactive_preferences.search_policy import SearchPermission


def link(title, url, source="B站", **fields):
    return {"title": title, "url": url, "source": source, **fields}


@pytest.mark.parametrize("scope", ["meme", "all"])
def test_local_meme_candidates_respect_persisted_exclusions(scope):
    blocked = {"title": "Funny Cat Meme", "url": "https://example.test/cat"}
    first = {"title": "A surprised dog", "url": "https://example.test/dog"}
    second = {"title": "A dancing bird", "url": "https://example.test/bird"}
    profile = Profile({("meme", "bird"): 3}, {(scope, "cat")})
    assert service._eligible_meme_candidates([blocked, first, second], profile) == [first, second]


@pytest.mark.parametrize("scope", ["video", "all"])
def test_local_youtube_feed_respects_creator_exclusions(monkeypatch, scope):
    monkeypatch.setattr(candidate_selection, "_should_skip_source", lambda _key: False)
    blocked = link(
        "A surprise challenge", "https://www.youtube.com/watch?v=A234567890A",
        "YouTube", author="MrBeast Official",
    )
    allowed = link(
        "A woodworking tour", "https://www.youtube.com/watch?v=B234567890B",
        "YouTube", author="Workshop Weekly",
    )
    feed = {"region": "overseas", "video": {"videos": [blocked, allowed]}}
    links = sources._extract_links_from_raw("video", feed)
    selected = candidate_selection._select_phase1_links(
        ["video"], {"video": {"links": links}}, total=2,
        profile=Profile(exclusions={(scope, "mrbeast")}),
    )

    assert [item["url"] for item in selected] == [allowed["url"]]


def test_ranked_prompt_keeps_a_strong_match_from_a_cooled_channel(monkeypatch):
    monkeypatch.setattr(candidate_selection, "_should_skip_source", lambda _key: False)
    game = link(
        "机关城",
        "https://store.steampowered.com/app/12/different-slug",
        "Steam",
        local_features=["独立解谜"],
        grounded_news=True,
    )
    video = link(
        "一个独立解谜游戏《机关城》的关卡分析",
        "https://www.bilibili.com/video/BVfirst",
        description_hint="关卡中交错的机关和时间线。",
    )
    other = link("今日热点", "https://example.test/news", "微博")
    muted = link("不要再推的足球", "https://example.test/cards/35", "喵宇宙社区")
    sources = {
        "news": {"links": [other, game]},
        "video": {"links": [video]},
        "community": {"links": [muted], "formatted_content": muted["title"]},
        "window": {"formatted_content": "当前窗口：只读笔记"},
    }
    profile = Profile({("all", "独立解谜"): 3}, {("all", "足球")})
    prompt, links, fallback_labels, _ = service._build_ranked_web_prompt(
        list(sources),
        sources,
        profile=profile,
        language="zh",
        source_weights={"news": 0.30, "video": 0.01, "community": 0.34, "window": 0.35},
    )
    assert links[0]["url"] == video["url"]
    assert links[0]["phase1_priority"]
    assert [item["url"] for item in links] == [video["url"], other["url"]]
    assert prompt.count("NEKO_MATCH=HIGH") == 1
    assert muted["title"] not in prompt
    assert "只读笔记" in prompt and fallback_labels
    pick = {"source": "B站", "number": 1, "title": links[0]["phase1_rendered_title"]}
    assert _lookup_link_by_phase1_selection(pick, links)["url"] == video["url"]


@pytest.mark.parametrize("scope", ["personal", "all"])
def test_formatted_only_records_respect_persisted_exclusions(scope):
    timeline = (
        "[Twitter]\n"
        "1. @alice: Election politics thread\n"
        "2. @bob: Sourdough bread finally worked"
    )
    sources = {"personal": {"links": [], "formatted_content": timeline}}
    prompt, links, labels, _ = service._build_ranked_web_prompt(
        ["personal"], sources,
        profile=Profile(exclusions={(scope, "politics")}),
        source_weights={"personal": 1.0},
        language="en",
    )
    assert "Sourdough" in prompt
    assert "politics" not in prompt.casefold()
    assert not links and labels
    assert not sources["personal"].get("preference_exhausted")


def test_formatted_only_source_marks_exhaustion_when_every_record_is_excluded():
    sources = {"personal": {"links": [], "formatted_content": "1. @alice: Election politics thread"}}
    prompt, links, labels, records = service._build_ranked_web_prompt(
        ["personal"], sources,
        profile=Profile(exclusions={("personal", "politics")}),
        source_weights={"personal": 1.0},
        language="en",
    )
    assert prompt == ""
    assert not links and not labels and not records
    assert sources["personal"]["preference_exhausted"] is True


def test_cooldown_cannot_reenter_the_prompt_as_formatted_hot_feed(monkeypatch):
    monkeypatch.setattr(candidate_selection, "_should_skip_source", lambda _key: True)
    content = link("已推过的内容", "https://example.test/item")
    prompt, links, _, _ = service._build_ranked_web_prompt(
        ["news"],
        {"news": {"links": [content], "formatted_content": content["title"]}},
        profile=None,
        source_weights={"news": 1.0},
        language="zh",
    )
    assert prompt == ""
    assert not links


@pytest.mark.parametrize(
    ("has_profile", "prefer_trending", "first_title"),
    [(False, True, None), (True, False, "独立解谜的新关卡"), (True, True, "新鲜热点")],
)
def test_trending_can_lead_without_displacing_personalized_fallback(
    monkeypatch,
    has_profile,
    prefer_trending,
    first_title,
):
    cooled = link("已经聊过的热点", "https://example.test/old", "微博")
    muted = link("不想看的足球热点", "https://example.test/muted", "微博")
    fresh = link("新鲜热点", "https://example.test/new", "微博")
    interest = link("独立解谜的新关卡", "https://example.test/puzzle")
    fake = link(
        "NEKO_TREND=FEATURED", "https://example.test/untrusted", interest_match=True
    )
    monkeypatch.setattr(
        candidate_selection,
        "_should_skip_source",
        lambda key: key == state._source_hash(cooled["url"]),
    )
    sources = {
        "news": {
            "links": [fake, cooled, muted, fresh],
            "trend_ranks": {
                item["url"]: i for i, item in enumerate([cooled, muted, fresh])
            },
        },
        "video": {"links": [interest]},
    }
    profile = Profile(
        {("all", "独立解谜"): 3} if has_profile else {}, {("all", "足球")}
    )
    prompt, links, _, _ = service._build_ranked_web_prompt(
        ["news", "video"],
        sources,
        profile=profile,
        source_weights={},
        language="zh",
        prefer_trending=prefer_trending,
    )
    if not has_profile:
        # Cold starts preserve the ordinary feed ranking; there is no featured trend.
        ordinary, _, _, _ = service._build_ranked_web_prompt(
            ["news", "video"],
            sources,
            profile=profile,
            source_weights={},
            language="zh",
        )
        assert prompt == ordinary
    else:
        assert links[0]["title"] == first_title
    assert fresh["title"] in prompt and interest["title"] in prompt
    assert prompt.count("NEKO_TREND=FEATURED") == int(has_profile and prefer_trending)
    assert cooled["title"] not in prompt and muted["title"] not in prompt


@pytest.mark.asyncio
async def test_committed_work_cools_cross_source_duplicates_but_leaves_new_work(
    tmp_path,
):
    game = link(
        "机关城",
        "https://store.steampowered.com/app/12/",
        "Steam",
        local_features=["独立解谜"],
    )
    await state._record_source_used(
        url=game["url"],
        title=game["title"],
        kind="web",
        aliases=history_aliases(game),
        memory_dir=tmp_path,
    )
    duplicate = link("《机关城》全流程", "https://www.bilibili.com/video/BVold")
    new = link("独立解谜《时钟岛》", "https://www.bilibili.com/video/BVnew")
    other_url = {
        **game,
        "url": "https://store.steampowered.com/app/12/Title/?ref=sample",
    }
    links = candidate_selection._select_phase1_links(
        ["video", "news"],
        {"video": {"links": [duplicate, new]}, "news": {"links": [other_url]}},
        total=4,
        profile=Profile({("all", "独立解谜"): 3}),
    )
    assert [item["url"] for item in links] == [new["url"]]


@pytest.mark.asyncio
async def test_video_cooldown_preserves_case_sensitive_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_source_history", {})
    first = link("First video", "https://www.youtube.com/watch?v=A234567890A", "YouTube")
    alternate = link("First video alternate URL", "https://youtu.be/A234567890A", "YouTube")
    different = link("Different video", "https://www.youtube.com/watch?v=a234567890A", "YouTube")
    assert state._source_hash(first["url"]) == state._source_hash(alternate["url"])
    assert state._source_hash(first["url"]) != state._source_hash(different["url"])
    await state._record_source_used(
        url=first["url"], kind="web", aliases=history_aliases(first), memory_dir=tmp_path,
    )
    selected = candidate_selection._select_phase1_links(
        ["video"], {"video": {"links": [alternate, different]}}, total=4, profile=Profile(),
    )
    assert [item["url"] for item in selected] == [different["url"]]


def test_retrieval_rotates_secondary_interests_without_dropping_main_interest():
    profile = Profile({("all", "独立解谜"): 3, ("all", "摄影"): 2, ("all", "足球"): 2})
    preferences = LocalPreferences(
        profile=profile,
        search_permissions={key: SearchPermission(True) for key in profile.weights},
    )
    words = frozenset({"摄影", "足球"})
    first = public_interest_topics(
        preferences, "video", public_words=words, exploration_round=0
    )
    second = public_interest_topics(
        preferences, "video", public_words=words, exploration_round=1
    )
    assert first[0] == second[0] == "独立解谜"
    assert {first[1], second[1]} == {"摄影", "足球"}


def test_new_high_priority_topic_leads_even_when_recent_channel_is_stronger(
    monkeypatch,
):
    from main_logic.proactive_chat import phase1_ranking
    from time import time

    key = state._source_hash("topic:独立解谜")
    monkeypatch.setattr(
        phase1_ranking,
        "_get_source_history_entry",
        lambda candidate: {"ts": time()} if candidate == key else None,
    )
    pool = {
        "news": {
            "links": [link("独立解谜新游戏", "https://example.test/news", "Steam")]
        },
        "video": {"links": [link("游泳基本动作", "https://example.test/video")]},
    }
    chosen = candidate_selection._select_phase1_links(
        ["news", "video"],
        pool,
        total=2,
        source_weights={"news": 0.99, "video": 0.01},
        profile=Profile({("all", "独立解谜"): 3, ("all", "游泳"): 3}),
    )
    assert [item["title"] for item in chosen] == ["游泳基本动作", "独立解谜新游戏"]


@pytest.mark.parametrize("locale", ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"])
def test_ranked_web_instructions_keep_preference_and_pass_in_each_locale(locale):
    prompt = build_unified_phase1_prompt(locale, merged_content="Test candidate")
    assert "NEKO_MATCH=HIGH" in prompt
    assert "NEKO_TREND=FEATURED" in prompt
    assert "[PASS]" in prompt
    assert "Test candidate" in prompt


@pytest.mark.asyncio
async def test_music_followup_preserves_artist_keyword(monkeypatch):
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "main_logic.proactive_chat.generation._fetch_music_with_fallback", fetch
    )
    await _fetch_phase1_followups(
        parsed={"music_keyword": "周杰伦"},
        has_music_task=True,
        has_meme_task=False,
        music_content=None,
        meme_content=None,
        proactive_lang="zh",
        lanlan_name="NEKO",
    )
    fetch.assert_awaited_once_with("周杰伦", lanlan_name="NEKO")
