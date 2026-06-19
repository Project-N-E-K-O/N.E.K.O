import pytest

from main_logic.topic.materials import (
    _default_fetchers,
    enrich_topic_materials_online,
)


@pytest.mark.asyncio
async def test_enrich_topic_materials_online_uses_default_prepare_intents():
    calls = []

    async def fake_video(keyword, limit):
        calls.append(("video", keyword, limit))
        return {
            "success": True,
            "search": {
                "results": [{"title": "白车补漆避坑指南", "url": "https://example.test/video"}]
            },
        }

    async def fake_meme(keyword, limit):
        calls.append(("meme", keyword, limit))
        return {
            "success": True,
            "data": [
                {"title": "补漆翻车表情包", "url": "https://example.test/meme.jpg"}
            ],
            "keyword_used": keyword,
        }

    materials = [
        {
            "interest": "我的车是白色的，不会给我用差漆吧，有色差就不好看了",
            "keywords": ["补漆"],
        }
    ]

    enriched = await enrich_topic_materials_online(
        materials,
        fetchers={"video": fake_video, "meme": fake_meme},
        max_materials=1,
    )

    assert [call[0] for call in calls] == ["video", "meme"]
    hint = enriched[0]["material_hint"]
    assert "白车补漆避坑指南" in hint["summary"]
    assert hint["links"][0]["type"] == "video"
    assert hint["meme_keyword"] == "补漆"


@pytest.mark.asyncio
async def test_enrich_prefers_deep_query_over_keywords():
    # A big-model-derived deep_query (Phase-2 delivery-time deep search) is the
    # query, overriding the cheap keyword-joined floor query.
    calls = []

    async def fake_news(keyword, limit):
        calls.append(keyword)
        return {
            "success": True,
            "search": {
                "results": [{"title": "deep kw result", "url": "https://x.test/d"}]
            },
        }

    materials = [
        {
            "interest": "x",
            "keywords": ["floor", "kw"],
            "deep_query": "deep derived query 关键",
        }
    ]

    enriched = await enrich_topic_materials_online(
        materials, fetchers={"news": fake_news}, max_materials=1
    )

    assert calls == ["deep derived query 关键"]
    assert enriched[0]["online_query"] == "deep derived query 关键"


@pytest.mark.asyncio
async def test_enrich_topic_materials_online_respects_explicit_empty_fetchers(monkeypatch):
    from main_logic.topic import materials as topic_materials

    async def fail_default_fetchers(lang):
        raise AssertionError("default fetchers should not be used")

    monkeypatch.setattr(topic_materials, "_default_fetchers", fail_default_fetchers)

    materials = [
        {
            "interest": "留学",
            "hook": "接住用户对留学的犹豫",
        }
    ]

    enriched = await enrich_topic_materials_online(
        materials,
        fetchers={},
        max_materials=1,
    )

    assert enriched == materials


@pytest.mark.asyncio
async def test_enrich_topic_materials_online_uses_keywords_as_query_and_marks_online_angle():
    calls = []

    async def fake_news(keyword, limit):
        calls.append(keyword)
        return {
            "success": True,
            "search": {
                "results": [
                    {
                        "title": "年轻人买车先看通勤半径和养车成本",
                        "url": "https://example.test/car-cost",
                    }
                ]
            },
        }

    materials = [
        {
            "interest": "用户把买车和生活自由感联系在一起",
            "hook": "不要硬讲车，先接住不想被人生流程推着走",
            "keywords": ["年轻人", "买车", "通勤", "养车", "成本"],
        }
    ]

    enriched = await enrich_topic_materials_online(
        materials,
        fetchers={"news": fake_news},
        max_materials=1,
    )

    assert calls == ["年轻人 买车 通勤 养车 成本"]
    assert enriched[0]["online_used"] is True
    assert enriched[0]["online_query"] == "年轻人 买车 通勤 养车 成本"
    assert "通勤半径和养车成本" in enriched[0]["online_angle"]
    assert "必须自然借一个具体点" in enriched[0]["material_hint"]["summary"]


@pytest.mark.asyncio
async def test_enrich_topic_materials_online_localizes_material_hint_summary():
    async def fake_news(keyword, limit):
        return {
            "success": True,
            "search": {
                "results": [
                    {
                        "title": "quiet city moving checklist",
                        "url": "https://example.test/move",
                    }
                ]
            },
        }

    enriched = await enrich_topic_materials_online(
        [
            {
                "interest": "moving to a quiet city",
                "hook": "start from wanting quieter daily life",
                "keywords": ["quiet city", "moving checklist"],
            }
        ],
        fetchers={"news": fake_news},
        lang="en",
        max_materials=1,
    )

    summary = enriched[0]["material_hint"]["summary"]
    assert 'Found material related to "quiet city moving checklist"' in summary
    assert "do not turn the search result into a report" in summary
    assert "找到了" not in summary


@pytest.mark.asyncio
async def test_enrich_topic_materials_online_drops_unrelated_online_titles():
    async def fake_news(keyword, limit):
        return {
            "success": True,
            "search": {
                "results": [
                    {"title": "全球最神秘超市卖什么", "url": "https://example.test/offtopic"},
                    {"title": "吉利银河混动纯电怎么选", "url": "https://example.test/car"},
                ]
            },
        }

    materials = [
        {
            "interest": "吉利银河混动和纯电选择纠结",
        }
    ]

    enriched = await enrich_topic_materials_online(
        materials,
        fetchers={"news": fake_news},
        max_materials=1,
    )

    hint = enriched[0]["material_hint"]
    assert "吉利银河混动纯电怎么选" in hint["summary"]
    assert "全球最神秘超市" not in hint["summary"]


@pytest.mark.asyncio
async def test_default_topic_fetchers_keep_chinese_search_local_and_fallback_inside_topic_layer(monkeypatch):
    calls = []

    async def fake_baidu(keyword, limit):
        calls.append(("baidu", keyword, limit))
        return {"success": False, "error": "baidu unavailable", "results": []}

    async def fake_duckduckgo(keyword, limit):
        calls.append(("duckduckgo", keyword, limit))
        return {
            "success": True,
            "results": [
                {"title": "脑机接口大众款最新进展", "url": "https://example.test/bci"}
            ],
        }

    monkeypatch.setattr("utils.web_scraper.search_baidu", fake_baidu)
    monkeypatch.setattr("utils.web_scraper.search_duckduckgo", fake_duckduckgo)

    fetchers = await _default_fetchers("zh-CN")
    result = await fetchers["news"]("脑机接口大众款", 2)

    # mainland: baidu primary, DuckDuckGo (not the 429-prone Google) as fallback
    assert calls == [
        ("baidu", "脑机接口大众款", 2),
        ("duckduckgo", "脑机接口大众款", 2),
    ]
    assert result["success"] is True
    assert result["region"] == "china"
    assert result["search"]["results"][0]["title"] == "脑机接口大众款最新进展"


@pytest.mark.asyncio
async def test_default_topic_fetchers_use_non_mainland_search_for_traditional_chinese(monkeypatch):
    calls = []

    async def fake_baidu(keyword, limit):
        calls.append(("baidu", keyword, limit))
        return {
            "success": True,
            "results": [{"title": "大陆源", "url": "https://example.test/cn"}],
        }

    async def fake_duckduckgo(keyword, limit):
        calls.append(("duckduckgo", keyword, limit))
        return {
            "success": True,
            "results": [
                {"title": "台灣城市流行歌單", "url": "https://example.test/tw"}
            ],
        }

    monkeypatch.setattr("utils.web_scraper.search_baidu", fake_baidu)
    monkeypatch.setattr("utils.web_scraper.search_duckduckgo", fake_duckduckgo)

    fetchers = await _default_fetchers("zh-TW")
    result = await fetchers["news"]("台灣 城市流行", 2)

    # non-mainland (zh-TW) goes to DuckDuckGo, not Google
    assert calls == [("duckduckgo", "台灣 城市流行", 2)]
    assert result["success"] is True
    assert result["region"] == "non-china"
    assert result["search"]["results"][0]["title"] == "台灣城市流行歌單"


@pytest.mark.asyncio
async def test_default_topic_fetchers_pass_source_locale_to_media_fetchers(monkeypatch):
    calls = []

    async def fake_meme_content(*, keyword, limit, source_locale=None):
        calls.append(("meme", keyword, limit, source_locale))
        return {"success": False, "data": []}

    async def fake_music_content(*, keyword, limit, source_locale=None):
        calls.append(("music", keyword, limit, source_locale))
        return {"success": False, "data": []}

    monkeypatch.setattr("utils.meme_fetcher.fetch_meme_content", fake_meme_content)
    monkeypatch.setattr("utils.music_crawlers.fetch_music_content", fake_music_content)

    fetchers = await _default_fetchers("zh-CN")
    await fetchers["meme"]("补漆翻车", 2)
    await fetchers["music"]("周杰伦", 1)

    assert calls == [
        ("meme", "补漆翻车", 2, "zh-CN"),
        ("music", "周杰伦", 1, "zh-CN"),
    ]


@pytest.mark.asyncio
async def test_default_topic_fetchers_keep_traditional_chinese_locale_distinct(monkeypatch):
    calls = []

    async def fake_meme_content(*, keyword, limit, source_locale=None):
        calls.append(("meme", source_locale))
        return {"success": False, "data": []}

    monkeypatch.setattr("utils.meme_fetcher.fetch_meme_content", fake_meme_content)

    fetchers = await _default_fetchers("zh-TW")
    await fetchers["meme"]("補漆翻車", 2)

    assert calls == [("meme", "zh-TW")]


@pytest.mark.asyncio
async def test_default_topic_fetchers_pass_non_chinese_locale_to_media_fetchers(monkeypatch):
    calls = []

    async def fake_music_content(*, keyword, limit, source_locale=None):
        calls.append((keyword, limit, source_locale))
        return {"success": False, "data": []}

    monkeypatch.setattr("utils.music_crawlers.fetch_music_content", fake_music_content)

    fetchers = await _default_fetchers("ja")
    await fetchers["music"]("city pop", 2)

    assert calls == [("city pop", 2, "ja")]
