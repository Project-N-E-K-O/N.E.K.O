import asyncio
import json
import logging
from time import time
from unittest.mock import AsyncMock

import httpx
import pytest

from main_logic.proactive_chat import (
    candidate_selection,
    generation,
    interest_retrieval,
    service,
    sources,
)
from main_logic.proactive_chat.contracts import ProactiveChatCommand
from main_logic.recommendation_feedback import RecommendationStore
from utils.proactive_preferences.personalization import (
    LocalPreferences,
    RecommendationContext,
)
from utils.proactive_preferences.preference_model import PreferenceEvent, Profile
from utils.proactive_preferences.search_policy import SearchPermission
from utils.web_scraper import trending_content


@pytest.fixture(autouse=True)
def use_synthetic_word_list(monkeypatch):
    words = AsyncMock(
        return_value=frozenset({"独立解谜游戏", "机械键盘", "自由即兴", "游泳"})
    )
    monkeypatch.setattr(interest_retrieval, "fetch_public_search_words", words)


@pytest.mark.asyncio
async def test_guest_preference_recalls_candidates_and_reaches_phase_one(
    monkeypatch, tmp_path
):
    now = int(time())
    events = [
        PreferenceEvent("one", "独立解谜游戏", "all", "explicit", 1, now),
        PreferenceEvent("two", "仅在本机的试验项目", "all", "explicit", 1, now),
        PreferenceEvent("three", "竞技游戏", "video", "exclude", -1, now),
    ]
    store = RecommendationStore(tmp_path / "recommendations.sqlite3")
    store.save_preferences(
        "guest", events, permissions={events[0].id: SearchPermission(True, True)}
    )
    context = RecommendationContext(
        preferences=store.local_preferences({"key": "guest"})
    )
    hot = {
        "title": "今日热门",
        "url": "https://www.bilibili.com/video/BV1234567890",
        "source": "B站",
    }
    puzzle = {
        "title": "我做了一款独立解谜游戏",
        "url": "https://www.bilibili.com/video/BV1234567891",
        "source": "B站",
        "grounded_news": True,
    }
    blocked = {
        **puzzle,
        "title": "独立解谜游戏和竞技游戏",
        "url": "https://example.test/blocked",
    }

    async def baseline(mode, **_kwargs):
        if mode == "community":
            raise OSError("feed unavailable")
        return mode, {"links": [hot, puzzle] if mode == "home" else [hot]}

    monkeypatch.setattr(sources, "_fetch_source", baseline)
    news_search = AsyncMock(return_value=[])
    monkeypatch.setattr(interest_retrieval, "_search_news", news_search)
    monkeypatch.setattr(interest_retrieval, "is_china_region", lambda: True)
    video_search = AsyncMock(return_value=[hot, puzzle, blocked])
    monkeypatch.setattr(interest_retrieval, "search_bilibili_videos", video_search)
    community_search = AsyncMock(
        return_value=[
            {
                "id": "card",
                "title": "独立解谜游戏里的角色",
                "url": "https://community.example.test/cards/125",
            }
        ]
    )
    monkeypatch.setattr(
        interest_retrieval, "search_neko_community_cards", community_search
    )
    steam_search = AsyncMock(
        return_value=[
            {
                "title": "Puzzle Studio 独立解谜游戏",
                "url": "https://store.steampowered.com/app/123/",
                "description": "Rotate blocks.",
            }
        ]
    )
    monkeypatch.setattr(interest_retrieval, "search_steam_games", steam_search)
    monkeypatch.setattr(candidate_selection, "_should_skip_source", lambda _key: False)

    modes = ["news", "video", "home", "community"]
    result = await sources.collect_proactive_sources(
        command=ProactiveChatCommand(),
        enabled_modes=modes,
        lanlan_name="NEKO",
        log=logging.getLogger(__name__),
        community_locale="zh-CN",
        recommendations=context,
    )
    video_search.assert_awaited_once_with("独立解谜游戏", limit=6)
    steam_search.assert_awaited_once_with("独立解谜游戏", language="zh-CN", limit=6)
    news_search.assert_awaited_once_with("独立解谜游戏", china=True)
    assert result["news"]["links"][0]["source"] == "Steam"
    community_search.assert_awaited_once_with("独立解谜游戏", limit=6, locale="zh-CN")
    assert result["video"]["links"] == [{**puzzle, "interest_match": True}, hot]
    assert sum(link["url"] == puzzle["url"] for link in result["home"]["links"]) == 1
    prompt, selected, _, _ = service._build_ranked_web_prompt(
        modes,
        result,
        profile=context.preferences.profile,
        language="zh",
        source_weights={"news": 0.33, "video": 0.01, "home": 0.33, "community": 0.33},
    )
    assert any(
        item["mode"] == "community" and item["interest_match"] for item in selected
    )
    [puzzle_pick] = [item for item in selected if item["url"] == puzzle["url"]]
    assert puzzle_pick["phase1_priority"]
    assert "NEKO_MATCH=HIGH" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://www.bilibili.com/video/BV1234567891",
    "https://example.test/watch/puzzles",
])
async def test_exclusion_in_baseline_video_metadata_applies_to_search_duplicate(monkeypatch, url):
    video = {
        "title": "独立解谜游戏", "url": url, "source": "video feed",
    }
    baseline = {**video, "url": f"{url}/", "description_hint": "专题同时包含竞技游戏"}
    monkeypatch.setattr(sources, "_fetch_source", AsyncMock(return_value=(
        "video", {"links": [baseline], "formatted_content": baseline["description_hint"]},
    )))
    monkeypatch.setattr(sources, "collect_interest_candidates", AsyncMock(return_value={"video": [video]}))
    profile = Profile({("all", "独立解谜游戏"): 3}, {("video", "竞技游戏")})
    result = await sources.collect_proactive_sources(
        command=ProactiveChatCommand(), enabled_modes=["video"], lanlan_name="NEKO",
        log=logging.getLogger(__name__), recommendations=RecommendationContext(
            preferences=LocalPreferences(profile=profile),
        ),
    )
    assert result["video"]["links"] == []
    assert result["video"]["formatted_content"] == ""


@pytest.mark.asyncio
async def test_public_news_queries_have_budget_for_a_serialized_backend(monkeypatch):
    lock = asyncio.Lock()

    async def search(topic, *, china):
        async with lock:
            await asyncio.sleep(0.08)
            return [{"title": f"{topic}新闻", "url": f"https://example.test/{topic}"}]

    monkeypatch.setattr(interest_retrieval, "_search_news", search)
    monkeypatch.setattr(interest_retrieval, "_FETCH_TIMEOUT", 0.12)
    monkeypatch.setattr(interest_retrieval, "_NEWS_FETCH_TIMEOUT", 0.6)
    weights = {("all", "机械键盘"): 3, ("all", "游泳"): 3}
    preferences = LocalPreferences(
        profile=Profile(weights), search_permissions={key: SearchPermission(True) for key in weights},
    )
    result = await interest_retrieval.collect_interest_candidates(preferences, ["news"], locale="zh-CN")
    assert {item["title"] for item in result["news"]} == {"机械键盘新闻", "游泳新闻"}


@pytest.mark.asyncio
async def test_video_only_searches_a_public_interest_without_loading_other_catalogs(
    monkeypatch,
):
    missing = AsyncMock(side_effect=AssertionError("wrong provider catalog"))
    monkeypatch.setattr(interest_retrieval, "fetch_public_search_words", missing)
    monkeypatch.setattr(interest_retrieval, "is_china_region", lambda: True)
    search = AsyncMock(
        return_value=[
            {
                "title": "游泳入门",
                "url": "https://www.bilibili.com/video/BV1234567890",
                "source": "B站",
            }
        ]
    )
    monkeypatch.setattr(interest_retrieval, "search_bilibili_videos", search)
    result = await interest_retrieval.collect_interest_candidates(
        LocalPreferences(
            profile=Profile(
                {("all", "游泳"): 3, ("all", "本机保密项目"): 3, ("all", "抑郁症"): 3}
            ),
            search_permissions={
                ("all", "游泳"): SearchPermission(True),
                ("all", "本机保密项目"): SearchPermission(False),
                ("video", "本机保密项目"): SearchPermission(True),
                ("all", "抑郁症"): SearchPermission(True),
            },
        ),
        ["video"],
        locale="zh-CN",
    )
    search.assert_awaited_once_with("游泳", limit=6)
    missing.assert_not_awaited()
    assert result["video"][0]["title"] == "游泳入门"


@pytest.mark.asyncio
async def test_media_candidates_follow_original_phase_one_keywords(monkeypatch):
    music = AsyncMock(
        return_value={
            "success": True,
            "data": [{"name": "绵延", "artist": "测试音乐人"}],
        }
    )
    memes = AsyncMock(
        return_value={
            "success": True,
            "data": [{"title": "游泳", "url": "https://example.test/a"}],
        }
    )
    monkeypatch.setattr(generation, "_fetch_music_with_fallback", music)
    monkeypatch.setattr(generation, "_fetch_meme_with_fallback", memes)
    context = RecommendationContext()
    context.preferences.profile = Profile(
        {("music", "自由即兴"): 3, ("all", "游泳"): 3}
    )
    log = logging.getLogger(__name__)
    collected = await sources.collect_proactive_sources(
        command=ProactiveChatCommand(),
        enabled_modes=["music", "meme"],
        lanlan_name="NEKO",
        log=log,
        community_locale="zh",
        recommendations=context,
    )
    assert collected["music"]["placeholder"]
    assert collected["meme"]["placeholder"]
    music.assert_not_awaited()
    memes.assert_not_awaited()
    music_result, meme_result = await generation._fetch_phase1_followups(
        parsed={"music_keyword": "随便听听", "meme_keyword": "大笑"},
        has_music_task=True,
        has_meme_task=True,
        music_content=None,
        meme_content=None,
        proactive_lang="zh",
        lanlan_name="NEKO",
        log=log,
    )
    music.assert_awaited_once_with("随便听听", lanlan_name="NEKO")
    memes.assert_awaited_once_with("大笑", lanlan_name="NEKO", log=log)
    assert music_result["raw_data"]["data"]
    assert meme_result["data"]


@pytest.mark.asyncio
async def test_failed_retrieval_keeps_regular_candidates_and_never_queries_private_topics(
    monkeypatch,
):
    profile = Profile({("news", "机械键盘"): 3, ("all", "仅在本机的项目"): 3})
    ordinary = {
        "title": "普通资讯",
        "url": "https://example.test/item",
        "source": "test",
    }
    monkeypatch.setattr(
        sources,
        "_fetch_source",
        AsyncMock(return_value=("news", {"links": [ordinary]})),
    )
    fetch = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(interest_retrieval, "_fetch_topic", fetch)
    monkeypatch.setattr(interest_retrieval, "is_china_region", lambda: True)
    context = RecommendationContext()
    context.preferences.profile = profile
    context.preferences.explicit_topics = {("news", "机械键盘")}
    result = await sources.collect_proactive_sources(
        command=ProactiveChatCommand(),
        enabled_modes=["news"],
        lanlan_name="NEKO",
        log=logging.getLogger(__name__),
        community_locale="zh-CN",
        recommendations=context,
    )
    assert result["news"]["links"] == [ordinary]
    fetch.assert_awaited_once_with("news", "机械键盘", "zh-CN", True)
    assert (
        profile.rank("news", [ordinary, {"title": "仅在本机的项目进展"}])[0]["title"]
        == "仅在本机的项目进展"
    )


@pytest.mark.asyncio
async def test_community_search_returns_public_permalinks_without_credentials(
    monkeypatch,
):
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "cards": [
                    {
                        "id": "a",
                        "serial_no": 125,
                        "visibility": "public",
                        "title": "独立解谜游戏",
                    },
                    {
                        "id": "b",
                        "serial_no": 126,
                        "visibility": "hidden",
                        "title": "隐藏卡",
                    },
                ]
            },
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        trending_content, "social_base_url", lambda: "https://community.example.test"
    )
    monkeypatch.setattr(
        trending_content.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(reply), **kwargs),
    )
    [card] = await trending_content.search_neko_community_cards(
        "独立解谜游戏", limit=6, locale="zh-CN"
    )
    assert card["url"] == "https://community.example.test/cards/125"
    [request] = requests
    assert request.url.params["q"] == "独立解谜游戏"
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers


@pytest.mark.asyncio
async def test_community_searches_full_phrase_and_respects_exclusions(monkeypatch):
    nearby = {"title": "独立解谜故事", "url": "https://example.test/cards/125"}
    unrelated = {"title": "不相关的故事", "url": "https://example.test/cards/130"}
    search = AsyncMock(return_value=[nearby, unrelated])
    monkeypatch.setattr(interest_retrieval, "search_neko_community_cards", search)
    profile = Profile({("all", "独立解谜"): 3})
    preferences = LocalPreferences(
        profile=profile,
        search_permissions={("all", "独立解谜"): SearchPermission(True)},
    )
    found = await interest_retrieval.collect_interest_candidates(
        preferences, ["community"], locale="zh-CN"
    )
    assert found["community"] == [{**nearby, "interest_match": True}]
    profile.exclusions.add(("community", "解谜"))
    assert not await interest_retrieval.collect_interest_candidates(
        preferences, ["community"], locale="zh-CN"
    )
    assert [x.args[0] for x in search.await_args_list] == ["独立解谜", "独立解谜"]


@pytest.mark.asyncio
async def test_cold_start_fetches_existing_feeds_and_preserves_trend_origin(
    monkeypatch,
):
    baseline = {
        "title": "首页热点",
        "url": "https://example.test/hot",
        "source": "微博",
    }
    personalized = {
        "title": "独立解谜新作",
        "url": "https://example.test/puzzle",
        "source": "微博",
    }
    fetch = AsyncMock(return_value=("news", {"links": [baseline]}))
    search = AsyncMock(return_value=[personalized])
    monkeypatch.setattr(sources, "_fetch_source", fetch)
    monkeypatch.setattr(interest_retrieval, "_search_news", search)
    kwargs = {
        "command": ProactiveChatCommand(),
        "enabled_modes": ["news"],
        "lanlan_name": "NEKO",
        "log": logging.getLogger(__name__),
    }
    empty = await sources.collect_proactive_sources(
        **kwargs, recommendations=RecommendationContext()
    )
    fetch.assert_awaited_once()
    search.assert_not_awaited()
    assert empty["news"]["links"] == [baseline]

    prefs = LocalPreferences(
        profile=Profile({("all", "独立解谜"): 3}),
        search_permissions={("all", "独立解谜"): SearchPermission(True)},
    )
    found = await sources.collect_proactive_sources(
        **kwargs,
        recommendations=RecommendationContext(preferences=prefs),
    )
    assert found["news"]["links"][0]["url"] == personalized["url"]
    assert found["news"]["trend_ranks"] == {baseline["url"]: 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    [
        {"community": "https://verse.test", "bearer": "test-token"},
        {"community_identity_known": False},
    ],
)
async def test_account_and_unknown_state_skip_anonymous_interest_recall(
    monkeypatch, session
):
    search = AsyncMock(
        return_value=[{"title": "独立解谜", "url": "https://verse.test/cards/1"}]
    )
    monkeypatch.setattr(interest_retrieval, "search_neko_community_cards", search)
    account_pick = {"title": "可见卡牌", "url": "https://verse.test/cards/2"}
    monkeypatch.setattr(
        sources,
        "_fetch_source",
        AsyncMock(
            return_value=(
                "community",
                {"links": [account_pick]},
            )
        ),
    )
    context = RecommendationContext(
        preferences=LocalPreferences(
            profile=Profile({("all", "独立解谜"): 3}),
            search_permissions={("all", "独立解谜"): SearchPermission(True)},
        ),
        **session,
    )
    fetched = await sources.collect_proactive_sources(
        command=ProactiveChatCommand(),
        enabled_modes=["community"],
        lanlan_name="NEKO",
        log=logging.getLogger(__name__),
        recommendations=context,
    )
    assert fetched["community"]["links"] == [account_pick]
    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_public_flag_cannot_search_an_unverified_phrase(monkeypatch):
    topic = "蓝鸥苑六号楼"
    search = AsyncMock()
    monkeypatch.setattr(interest_retrieval, "search_neko_community_cards", search)
    monkeypatch.setattr(
        interest_retrieval,
        "fetch_public_search_words",
        AsyncMock(
            return_value=frozenset({"蓝鸥苑", "六号楼"}),
        ),
    )
    preferences = LocalPreferences(
        profile=Profile({("all", topic): 3}),
        search_permissions={("all", topic): SearchPermission(True)},
    )
    assert not await interest_retrieval.collect_interest_candidates(
        preferences, ["community"], locale="zh-CN"
    )
    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_recent_attention_from_chat_reserves_a_search_and_ranking_slot(
    monkeypatch, tmp_path
):
    from main_logic.proactive_chat import preference_capture

    now = int(time())
    store = RecommendationStore(tmp_path / "recommendations.sqlite3")
    history = (
        "[P3] User | 我喜欢机械键盘。\n"
        "[P2] User | 游泳时换气老是沉下去，有没有技巧？\n"
        "[P1] User | 我在研究未公开的工作项目。"
    )
    results = [
        {
            "source": number,
            "feature": feature,
            "scope": "all",
            "kind": kind,
            "direction": 1,
            "confidence": 0.95,
            "public": public,
            "game": False,
        }
        for number, feature, kind, public in (
            (3, "机械键盘", "explicit", True),
            (2, "游泳", "inferred", True),
            (1, "未公开的工作项目", "inferred", False),
        )
    ]
    model = AsyncMock(
        return_value="[WEB] [PASS]\n[PREFERENCES] "
        + json.dumps(results, ensure_ascii=False)
    )
    monkeypatch.setattr(generation, "_llm_call_with_retry", model)
    config = generation.ProactiveModelConfig("text-test", None, "synthetic", None)
    parsed = await generation._run_unified_phase1(
        model_config=config,
        proactive_lang="zh",
        lanlan_name="Neko",
        master_name="User",
        merged_web_content="Some public candidate",
        memory_context=history,
        recent_chats_section="",
        has_music_task=False,
        has_meme_task=False,
        preference_evidence=True,
    )
    model.assert_awaited_once()
    prompt = model.await_args.kwargs["system_prompt"]
    assert prompt.count(history) == 1
    assert "[PREFERENCES]" in prompt
    assert parsed["web_pass"]
    refs = {1: ("private-id", now), 2: ("focus-id", now), 3: ("hobby-id", now)}
    owner = {"key": "guest"}
    store.save_chat_preferences(
        owner["key"],
        preference_capture.build_chat_evidence(parsed["preference_evidence"], refs),
    )
    monkeypatch.setattr(interest_retrieval, "is_china_region", lambda: True)

    async def video(query, *, limit):
        return [
            {
                "title": f"{query}教程",
                "url": f"https://video.test/{query}",
                "source": "B站",
            }
        ]

    search = AsyncMock(side_effect=video)
    monkeypatch.setattr(interest_retrieval, "search_bilibili_videos", search)
    preferences = store.local_preferences(owner)
    result = await interest_retrieval.collect_interest_candidates(
        preferences, ["video"], locale="zh-CN"
    )
    assert [call.args[0] for call in search.await_args_list] == ["游泳", "机械键盘"]
    assert [item["title"] for item in result["video"]] == ["游泳教程", "机械键盘教程"]
    selected = candidate_selection._select_phase1_links(
        ["video"],
        {"video": {"links": result["video"]}},
        total=1,
        profile=preferences.profile,
    )
    assert selected[0]["title"] == "游泳教程"
    assert selected[0]["phase1_priority"]
