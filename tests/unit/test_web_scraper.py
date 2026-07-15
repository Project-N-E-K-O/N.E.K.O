import os
import sys

import pytest
from utils.llm_client import AIMessage, HumanMessage, SystemMessage


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import utils.config_manager as config_manager_module
import utils.web_scraper as web_scraper
import utils.web_scraper.trending_content as trending_content
import utils.web_scraper.window_context as window_context


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_diverse_queries_sends_user_message(monkeypatch):
    captured = {}

    class FakeConfigManager:
        def get_model_api_config(self, model_type):
            assert model_type == "summary"
            return {
                "model": "gemini-3-flash-preview",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "api_key": "test-key",
            }

    class FakeLLM:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content="关键词A\n关键词B\n关键词C")

    def fake_create_chat_llm(*args, **kwargs):
        return FakeLLM(**kwargs)

    monkeypatch.setattr(config_manager_module, "ConfigManager", FakeConfigManager)
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)
    monkeypatch.setattr(window_context, "is_china_region", lambda: True)

    result = await web_scraper.generate_diverse_queries("Project N.E.K.O.")

    assert result == ["关键词A", "关键词B", "关键词C"]
    assert len(captured["messages"]) == 2
    assert isinstance(captured["messages"][0], SystemMessage)
    assert isinstance(captured["messages"][1], HumanMessage)
    assert "Project N.E.K.O." in captured["messages"][1].content


class _FakeTiebaThread:
    def __init__(
        self,
        tid,
        title,
        *,
        text="",
        reply_num=0,
        view_num=0,
        is_top=False,
    ):
        self.tid = tid
        self.title = title
        self.text = text
        self.reply_num = reply_num
        self.view_num = view_num
        self.is_top = is_top


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_news_content_merges_weibo_and_tieba_in_china(monkeypatch):
    async def fake_weibo(limit):
        return {
            "success": True,
            "trending": [{"word": "微博热搜", "url": "https://s.weibo.com/weibo?q=x"}],
        }

    async def fake_tieba(keyword="", limit=5, candidate_limit=None):
        return {
            "success": True,
            "posts": [{"title": "贴吧热门帖子", "url": "https://tieba.baidu.com/p/1"}],
            "topics": [],
            "tieba": {"success": True, "posts": [], "topics": []},
            "formatted_content": "【贴吧热门帖子（社区讨论，非权威信息）】\n1. 贴吧热门帖子",
        }

    monkeypatch.setattr(trending_content, "is_china_region", lambda: True)
    monkeypatch.setattr(trending_content, "fetch_weibo_trending", fake_weibo)
    monkeypatch.setattr(trending_content, "fetch_tieba_content", fake_tieba)

    result = await web_scraper.fetch_news_content(limit=3)
    formatted = web_scraper.format_news_content(result)

    assert result["success"] is True
    assert result["region"] == "china"
    assert result["news"]["trending"][0]["word"] == "微博热搜"
    assert result["tieba"]["posts"][0]["title"] == "贴吧热门帖子"
    assert "微博热搜" in formatted
    assert "贴吧热门帖子" in formatted
    assert "社区讨论" in formatted
    assert "非权威" in formatted


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_news_content_succeeds_when_weibo_fails_but_tieba_succeeds(monkeypatch):
    async def fake_weibo(limit):
        return {"success": False, "error": "weibo blocked"}

    async def fake_tieba(keyword="", limit=5, candidate_limit=None):
        return {
            "success": True,
            "posts": [{"title": "贴吧候补", "url": "https://tieba.baidu.com/p/2"}],
            "topics": [],
            "tieba": {"success": True, "posts": [], "topics": []},
            "formatted_content": "【贴吧热门帖子（社区讨论，非权威信息）】\n1. 贴吧候补",
        }

    monkeypatch.setattr(trending_content, "is_china_region", lambda: True)
    monkeypatch.setattr(trending_content, "fetch_weibo_trending", fake_weibo)
    monkeypatch.setattr(trending_content, "fetch_tieba_content", fake_tieba)

    result = await web_scraper.fetch_news_content(limit=3)

    assert result["success"] is True
    assert result["news"]["success"] is False
    assert result["tieba"]["success"] is True
    assert "贴吧候补" in web_scraper.format_news_content(result)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_news_content_succeeds_when_tieba_fails_but_weibo_succeeds(monkeypatch):
    async def fake_weibo(limit):
        return {
            "success": True,
            "trending": [{"word": "微博仍可用", "url": "https://s.weibo.com/weibo?q=y"}],
        }

    async def fake_tieba(keyword="", limit=5, candidate_limit=None):
        return {"success": False, "error": "tieba blocked", "posts": [], "topics": []}

    monkeypatch.setattr(trending_content, "is_china_region", lambda: True)
    monkeypatch.setattr(trending_content, "fetch_weibo_trending", fake_weibo)
    monkeypatch.setattr(trending_content, "fetch_tieba_content", fake_tieba)

    result = await web_scraper.fetch_news_content(limit=3)

    assert result["success"] is True
    assert result["news"]["success"] is True
    assert result["tieba"]["success"] is False
    assert "微博仍可用" in web_scraper.format_news_content(result)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_tieba_content_uses_aiotieba_bars_and_hot_topics(monkeypatch):
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_threads(self, bar_name, pn=1, rn=30):
            calls.append((bar_name, pn, rn))
            if bar_name == "\u6e38\u620f\u653b\u7565":
                return [
                    _FakeTiebaThread("10", "\u7f6e\u9876\u516c\u544a", reply_num=999, view_num=9999, is_top=True),
                    _FakeTiebaThread("11", "\u540c\u57ce\u4fe1\u606f\u65b9\u4fbf", reply_num=50, view_num=5000),
                    _FakeTiebaThread("12", "\u653b\u7565\u8ba8\u8bbaA", text="\u793e\u533a\u6b63\u5728\u8ba8\u8bba\u7684\u89d2\u5ea6", reply_num=20, view_num=3000),
                    _FakeTiebaThread("12", "\u653b\u7565\u8ba8\u8bbaA", text="\u91cd\u590d\u5e16", reply_num=10, view_num=2000),
                ]
            if bar_name == "steam":
                return [_FakeTiebaThread("20", "\u9ed1\u795e\u8bdd\u70ed\u5ea6\u8ba8\u8bba", reply_num=300, view_num=50000)]
            return []

    async def fake_hot_topics(limit):
        return [
            {
                "title": "\u8d34\u5427\u70ed\u699c\u8bdd\u9898",
                "url": "https://tieba.baidu.com/hottopic/browse/hottopic?topic_id=1",
                "abstract": "\u7f51\u53cb\u6b63\u5728\u8ba8\u8bba",
                "source": "\u8d34\u5427",
                "reply_num": 1000,
                "view_num": 2000,
                "type": "topic",
            }
        ]

    async def fake_topic_posts(topics, limit):
        assert topics[0]["title"] == "\u8d34\u5427\u70ed\u699c\u8bdd\u9898"
        return [
            {
                "title": "\u70ed\u699c\u91cc\u89e3\u6790\u51fa\u7684\u5e16\u5b50",
                "url": "https://tieba.baidu.com/p/30",
                "abstract": "\u70ed\u699c\u8865\u5145",
                "source": "\u8d34\u5427",
                "bar_name": "\u70ed\u699c",
                "reply_num": 100,
                "view_num": 10000,
                "tid": "30",
                "type": "post",
            }
        ]

    monkeypatch.setattr(trending_content, "_get_aiotieba_client_class", lambda: FakeClient)
    monkeypatch.setattr(trending_content, "_fetch_tieba_hot_topics", fake_hot_topics)
    monkeypatch.setattr(trending_content, "_fetch_tieba_topic_posts", fake_topic_posts)

    result = await web_scraper.fetch_tieba_content("\u6e38\u620f\u653b\u7565", limit=3)

    assert calls[0][0] == "\u6e38\u620f\u653b\u7565"
    assert any(call[0] == "\u539f\u795e" for call in calls)
    assert result["success"] is True
    assert len(result["posts"]) == 3
    assert result["posts"][0]["title"] == "\u9ed1\u795e\u8bdd\u70ed\u5ea6\u8ba8\u8bba"
    assert all(post["source"] == "\u8d34\u5427" for post in result["posts"])
    assert all("\u540c\u57ce" not in post["title"] for post in result["posts"])
    assert len({post["url"] for post in result["posts"]}) == len(result["posts"])
    assert result["topics"][0]["title"] == "\u8d34\u5427\u70ed\u699c\u8bdd\u9898"
    assert "\u793e\u533a\u8ba8\u8bba" in result["formatted_content"]
    assert "\u975e\u6743\u5a01" in result["formatted_content"]
    assert "https://tieba.baidu.com/p/" in result["formatted_content"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_tieba_content_allows_partial_bar_failure(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_threads(self, bar_name, pn=1, rn=30):
            if bar_name == "\u539f\u795e":
                raise RuntimeError("blocked")
            if bar_name == "steam":
                return [_FakeTiebaThread("20", "\u53ef\u7528\u8ba8\u8bba", reply_num=3, view_num=300)]
            return []

    async def fake_hot_topics(limit):
        return []

    async def fake_topic_posts(topics, limit):
        return []

    monkeypatch.setattr(trending_content, "_get_aiotieba_client_class", lambda: FakeClient)
    monkeypatch.setattr(trending_content, "_fetch_tieba_hot_topics", fake_hot_topics)
    monkeypatch.setattr(trending_content, "_fetch_tieba_topic_posts", fake_topic_posts)

    result = await web_scraper.fetch_tieba_content(limit=2)

    assert result["success"] is True
    assert result["posts"][0]["title"] == "\u53ef\u7528\u8ba8\u8bba"
    assert "warnings" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_tieba_content_candidate_pool_is_larger_than_display(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_threads(self, bar_name, pn=1, rn=30):
            if bar_name == "\u539f\u795e":
                return [
                    _FakeTiebaThread("101", "\u6bcf\u65e5\u6c34\u697c", reply_num=999, view_num=50000),
                    _FakeTiebaThread("102", "\u5982\u4f55\u8bc4\u4ef7\u65b0\u7248\u672c\u5267\u60c5", reply_num=5, view_num=500),
                    _FakeTiebaThread("103", "\u666e\u901a\u9ad8\u70ed\u6807\u9898", reply_num=200, view_num=10000),
                ]
            if bar_name == "\u660e\u65e5\u65b9\u821f":
                return [_FakeTiebaThread("201", "\u65b0\u624b\u653b\u7565\u8ba8\u8bba", reply_num=3, view_num=300)]
            if bar_name == "steam":
                return [
                    _FakeTiebaThread("301", "\u957f\u671f\u697c\u8bb0\u5f55", reply_num=500, view_num=80000),
                    _FakeTiebaThread("302", "\u6709\u6ca1\u6709\u9002\u5408\u5165\u5751\u7684\u6e38\u620f", reply_num=2, view_num=260),
                ]
            return []

    async def fake_hot_topics(limit):
        return []

    async def fake_topic_posts(topics, limit):
        return []

    monkeypatch.setattr(trending_content, "_get_aiotieba_client_class", lambda: FakeClient)
    monkeypatch.setattr(trending_content, "_fetch_tieba_hot_topics", fake_hot_topics)
    monkeypatch.setattr(trending_content, "_fetch_tieba_topic_posts", fake_topic_posts)

    result = await web_scraper.fetch_tieba_content(limit=2, candidate_limit=4)

    assert result["success"] is True
    assert result["display_limit"] == 2
    assert result["candidate_limit"] == 4
    assert len(result["posts"]) == 4
    assert "\u6bcf\u65e5\u6c34\u697c" not in {post["title"] for post in result["posts"]}
    assert "\u957f\u671f\u697c\u8bb0\u5f55" not in {post["title"] for post in result["posts"]}
    assert result["posts"][0]["title"] == "\u5982\u4f55\u8bc4\u4ef7\u65b0\u7248\u672c\u5267\u60c5"
    assert len({post["bar_name"] for post in result["posts"][:3]}) == 3
    assert result["formatted_content"].count("https://tieba.baidu.com/p/") == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_tieba_content_reports_all_source_failure(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_threads(self, bar_name, pn=1, rn=30):
            raise RuntimeError("blocked")

    async def fake_hot_topics(limit):
        raise RuntimeError("captcha")

    monkeypatch.setattr(trending_content, "_get_aiotieba_client_class", lambda: FakeClient)
    monkeypatch.setattr(trending_content, "_fetch_tieba_hot_topics", fake_hot_topics)

    result = await web_scraper.fetch_tieba_content(limit=3)

    assert result["success"] is False
    assert result["posts"] == []
    assert result["topics"] == []
    assert result["tieba"]["posts"] == []
    assert result["tieba"]["topics"] == []
    assert "blocked" in result["error"]
