import os
import sys

import pytest
from utils.llm_client import AIMessage, HumanMessage, SystemMessage


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import utils.config_manager as config_manager_module
import utils.web_scraper as web_scraper
import utils.web_scraper.trending_content as trending_content
import utils.web_scraper.window_context as window_context


@pytest.fixture(autouse=True)
def clear_tieba_recent_keys():
    trending_content._TIEBA_RECENT_KEYS.clear()
    yield
    trending_content._TIEBA_RECENT_KEYS.clear()


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


class _FakeTiebaComment:
    def __init__(self, text, *, agree=0, create_time=0):
        self.text = text
        self.agree = agree
        self.create_time = create_time


class _FakeTiebaDetailPost:
    def __init__(
        self,
        text,
        *,
        floor=0,
        agree=0,
        reply_num=0,
        create_time=0,
        is_thread_author=False,
        comments=None,
    ):
        self.text = text
        self.floor = floor
        self.agree = agree
        self.reply_num = reply_num
        self.create_time = create_time
        self.is_thread_author = is_thread_author
        self.comments = comments or []


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
async def test_fetch_news_content_routes_non_china_to_twitter(monkeypatch):
    async def fake_weibo(limit):
        raise AssertionError("non-China news must not fetch Weibo")

    async def fake_tieba(keyword="", limit=5, candidate_limit=None):
        raise AssertionError("non-China news must not fetch Tieba")

    async def fake_twitter(limit):
        return {
            "success": True,
            "trending": [{"word": "Global trend", "url": "https://twitter.com/search?q=x"}],
        }

    monkeypatch.setattr(trending_content, "is_china_region", lambda: False)
    monkeypatch.setattr(trending_content, "fetch_weibo_trending", fake_weibo)
    monkeypatch.setattr(trending_content, "fetch_tieba_content", fake_tieba)
    monkeypatch.setattr(trending_content, "fetch_twitter_trending", fake_twitter)

    result = await web_scraper.fetch_news_content(limit=3)

    assert result["success"] is True
    assert result["region"] == "non-china"
    assert result["news"]["trending"][0]["word"] == "Global trend"


@pytest.mark.unit
def test_format_tieba_content_respects_topic_display_budget():
    full_posts = web_scraper.format_tieba_content(
        {
            "success": True,
            "display_limit": 2,
            "posts": [
                {"title": "Post A", "url": "https://tieba.baidu.com/p/a"},
                {"title": "Post B", "url": "https://tieba.baidu.com/p/b"},
            ],
            "topics": [
                {"title": "Topic A", "url": "https://tieba.baidu.com/hottopic/a"},
            ],
        }
    )

    assert "Post A" in full_posts
    assert "Post B" in full_posts
    assert "Topic A" not in full_posts

    one_remaining = web_scraper.format_tieba_content(
        {
            "success": True,
            "display_limit": 2,
            "posts": [
                {"title": "Post A", "url": "https://tieba.baidu.com/p/a"},
            ],
            "topics": [
                {"title": "Topic A", "url": "https://tieba.baidu.com/hottopic/a"},
                {"title": "Topic B", "url": "https://tieba.baidu.com/hottopic/b"},
            ],
        }
    )

    assert "Post A" in one_remaining
    assert "Topic A" in one_remaining
    assert "Topic B" not in one_remaining


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_tieba_content_uses_aiotieba_bars_and_hot_topics(monkeypatch):
    calls = []
    client_kwargs = []

    class FakeClient:
        def __init__(self, **kwargs):
            client_kwargs.append(kwargs)

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
    assert client_kwargs
    assert all(kwargs == {"proxy": True} for kwargs in client_kwargs)
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
        def __init__(self, **kwargs):
            assert kwargs == {"proxy": True}

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
        def __init__(self, **kwargs):
            assert kwargs == {"proxy": True}

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
async def test_fetch_tieba_content_reuses_recent_candidates_when_pool_is_static(monkeypatch):
    async def fake_bar_posts(bar_name, *, rn):
        if bar_name == "\u539f\u795e":
            return [
                {
                    "title": "\u9759\u6001\u5019\u9009\u8ba8\u8bba",
                    "url": "https://tieba.baidu.com/p/777",
                    "abstract": "\u5c0f\u5019\u9009\u6c60\u4ecd\u7136\u5e94\u8be5\u53ef\u7528",
                    "source": "\u8d34\u5427",
                    "bar_name": "\u539f\u795e",
                    "reply_num": 10,
                    "view_num": 1000,
                    "tid": "777",
                    "type": "post",
                    "origin": "bar",
                }
            ]
        return []

    async def fake_hot_topics(limit):
        return []

    async def fake_topic_posts(topics, limit):
        return []

    async def fake_enrich(posts, errors):
        return None

    monkeypatch.setattr(trending_content, "_fetch_tieba_bar_posts", fake_bar_posts)
    monkeypatch.setattr(trending_content, "_fetch_tieba_hot_topics", fake_hot_topics)
    monkeypatch.setattr(trending_content, "_fetch_tieba_topic_posts", fake_topic_posts)
    monkeypatch.setattr(trending_content, "_enrich_tieba_posts_with_hot_replies", fake_enrich)

    first = await web_scraper.fetch_tieba_content(limit=1)
    second = await web_scraper.fetch_tieba_content(limit=1)

    assert first["success"] is True
    assert second["success"] is True
    assert second["posts"][0]["title"] == "\u9759\u6001\u5019\u9009\u8ba8\u8bba"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_tieba_content_enriches_top_three_posts_with_hot_replies(monkeypatch):
    detail_calls = []
    client_kwargs = []

    async def fake_bar_posts(bar_name, *, rn):
        if bar_name != "\u539f\u795e":
            return []
        return [
            {
                "title": f"\u70ed\u95e8\u8ba8\u8bba{i}",
                "url": f"https://tieba.baidu.com/p/{i}",
                "abstract": "\u8fd9\u662f\u5e16\u5b50\u6458\u8981",
                "source": "\u8d34\u5427",
                "bar_name": f"bar-{i}",
                "reply_num": 30 - i,
                "view_num": 3000 - i,
                "tid": str(i),
                "type": "post",
                "origin": "bar",
            }
            for i in range(1, 5)
        ]

    async def fake_hot_topics(limit):
        return []

    async def fake_topic_posts(topics, limit):
        return []

    class FakeClient:
        def __init__(self, **kwargs):
            client_kwargs.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_posts(self, tid, pn=1, **kwargs):
            detail_calls.append((tid, pn, kwargs))
            assert pn == 1
            assert kwargs["rn"] == 12
            assert getattr(kwargs["sort"], "name", "") == "HOT"
            assert kwargs["with_comments"] is True
            assert kwargs["comment_sort_by_agree"] is True
            assert kwargs["comment_rn"] == 3
            return [
                _FakeTiebaDetailPost(
                    "\u7b2c\u4e00\u6761\u70ed\u95e8\u697c\u5c42\u89c2\u70b9\u5f88\u5177\u4f53",
                    floor=9,
                    agree=1,
                    reply_num=6,
                    create_time=101,
                    comments=[
                        _FakeTiebaComment("\u8fd9\u4e2a\u53cd\u5e94\u4e5f\u633a\u6709\u4fe1\u606f\u91cf", agree=8, create_time=201),
                        _FakeTiebaComment("\u592a\u77ed", agree=99, create_time=202),
                        _FakeTiebaComment("\u53e6\u4e00\u4e2a\u56f4\u89c2\u89d2\u5ea6\u4e5f\u80fd\u7528", agree=7, create_time=203),
                    ],
                ),
                _FakeTiebaDetailPost(
                    "\u7b2c\u4e8c\u6761\u5e94\u8be5\u4fdd\u6301HOT\u8fd4\u56de\u987a\u5e8f",
                    floor=2,
                    agree=100,
                    reply_num=1,
                    create_time=102,
                ),
                _FakeTiebaDetailPost("\u7b2c\u4e09\u6761\u70ed\u95e8\u697c\u5c42\u5185\u5bb9", floor=5, agree=30),
                _FakeTiebaDetailPost("\u7b2c\u56db\u6761\u8d85\u51fa\u4e0a\u9650\u5e94\u8be5\u88ab\u622a\u6389", floor=6, agree=40),
            ]

    monkeypatch.setattr(trending_content, "_fetch_tieba_bar_posts", fake_bar_posts)
    monkeypatch.setattr(trending_content, "_fetch_tieba_hot_topics", fake_hot_topics)
    monkeypatch.setattr(trending_content, "_fetch_tieba_topic_posts", fake_topic_posts)
    monkeypatch.setattr(trending_content, "_get_aiotieba_client_class", lambda: FakeClient)

    result = await web_scraper.fetch_tieba_content(limit=2, candidate_limit=4)

    assert [call[0] for call in detail_calls] == [1, 2, 3]
    assert client_kwargs == [{"proxy": True}]
    assert "hot_replies" in result["posts"][0]
    assert "hot_replies" not in result["posts"][3]
    first_replies = result["posts"][0]["hot_replies"]
    assert len(first_replies) == 3
    assert [reply["floor"] for reply in first_replies[:2]] == [9, 2]
    assert len(first_replies[0]["reactions"]) == 2
    assert "\u70ed\u95e8\u56de\u590d" in result["formatted_content"]
    assert "\u53cd\u5e94\uff1a" in result["formatted_content"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_tieba_content_keeps_posts_when_hot_reply_fetch_fails(monkeypatch):
    async def fake_bar_posts(bar_name, *, rn):
        if bar_name == "\u539f\u795e":
            return [
                {
                    "title": "\u53ef\u7528\u8ba8\u8bba\u5e16",
                    "url": "https://tieba.baidu.com/p/99",
                    "abstract": "\u5e16\u5b50\u672c\u8eab\u53ef\u7528",
                    "source": "\u8d34\u5427",
                    "bar_name": "\u539f\u795e",
                    "reply_num": 10,
                    "view_num": 1000,
                    "tid": "99",
                    "type": "post",
                    "origin": "bar",
                }
            ]
        return []

    async def fake_hot_topics(limit):
        return []

    async def fake_topic_posts(topics, limit):
        return []

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs == {"proxy": True}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_posts(self, tid, pn=1, **kwargs):
            raise RuntimeError("detail blocked")

    monkeypatch.setattr(trending_content, "_fetch_tieba_bar_posts", fake_bar_posts)
    monkeypatch.setattr(trending_content, "_fetch_tieba_hot_topics", fake_hot_topics)
    monkeypatch.setattr(trending_content, "_fetch_tieba_topic_posts", fake_topic_posts)
    monkeypatch.setattr(trending_content, "_get_aiotieba_client_class", lambda: FakeClient)

    result = await web_scraper.fetch_tieba_content(limit=1)

    assert result["success"] is True
    assert result["posts"][0]["title"] == "\u53ef\u7528\u8ba8\u8bba\u5e16"
    assert "hot_replies" not in result["posts"][0]
    assert "warnings" in result
    assert "detail blocked" in "; ".join(result["warnings"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_tieba_content_reports_all_source_failure(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs == {"proxy": True}

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
