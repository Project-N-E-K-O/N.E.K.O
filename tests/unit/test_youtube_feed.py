import hashlib

import httpx
import pytest

from main_routers.system_router.proactive_parsing import _extract_links_from_raw
from utils import cookies_login
from utils.web_scraper import trending_content
from utils.web_scraper import youtube_feed


def test_extract_ytcfg_merges_bootstrap_objects():
    html = """
    <script>ytcfg.set({"INNERTUBE_API_KEY":"key-1"});</script>
    <script>ytcfg.set( {"INNERTUBE_CONTEXT":{"client":{"clientVersion":"1.2.3"}}} );</script>
    """

    config = youtube_feed._extract_ytcfg(html)

    assert config["INNERTUBE_API_KEY"] == "key-1"
    assert config["INNERTUBE_CONTEXT"]["client"]["clientVersion"] == "1.2.3"


def test_build_sapisid_authorization_supports_secure_cookie_fallback():
    expected_digest = hashlib.sha1(
        b"123456 secret https://www.youtube.com"
    ).hexdigest()

    authorization = youtube_feed._build_sapisid_authorization(
        {"__Secure-3PAPISID": "secret"}, now=123456
    )

    assert authorization == f"SAPISIDHASH 123456_{expected_digest}"


def test_extract_videos_supports_classic_and_lockup_renderers():
    payload = {
        "contents": [
            {
                "videoRenderer": {
                    "videoId": "classic123",
                    "title": {"runs": [{"text": "Classic video"}]},
                    "ownerText": {"runs": [{"text": "Creator A"}]},
                    "viewCountText": {"simpleText": "12K views"},
                    "publishedTimeText": {"simpleText": "2 hours ago"},
                    "thumbnail": {"thumbnails": [{"url": "https://img/classic.jpg"}]},
                }
            },
            {
                "lockupViewModel": {
                    "contentId": "lockup456",
                    "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
                    "metadata": {
                        "lockupMetadataViewModel": {
                            "title": {"content": "Lockup video"},
                            "metadata": {
                                "contentMetadataViewModel": {
                                    "metadataRows": [
                                        {
                                            "metadataParts": [
                                                {"text": {"content": "Creator B"}},
                                                {"text": {"content": "34K views"}},
                                            ]
                                        }
                                    ]
                                }
                            },
                        }
                    },
                    "contentImage": {
                        "thumbnailViewModel": {
                            "image": {"sources": [{"url": "https://img/lockup.jpg"}]}
                        }
                    },
                }
            },
        ]
    }

    videos = youtube_feed._extract_videos(payload, 10)

    assert [video["video_id"] for video in videos] == ["classic123", "lockup456"]
    assert videos[0]["author"] == "Creator A"
    assert videos[1]["author"] == "Creator B"
    assert videos[1]["source"] == "YouTube"


@pytest.mark.asyncio
async def test_fetch_youtube_home_feed_uses_anonymous_browse(monkeypatch):
    class FakeResponse:
        def __init__(self, *, text="", payload=None):
            self.text = text
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self):
            self.post_kwargs = None

        async def get(self, *_args, **_kwargs):
            return FakeResponse(
                text=(
                    '<script>ytcfg.set({"INNERTUBE_API_KEY":"api-key",'
                    '"INNERTUBE_CONTEXT":{"client":{"clientVersion":"1.2.3",'
                    '"visitorData":"visitor"}}});</script>'
                )
            )

        async def post(self, *_args, **kwargs):
            self.post_kwargs = kwargs
            return FakeResponse(payload={
                "videoRenderer": {
                    "videoId": "video123",
                    "title": {"simpleText": "Home recommendation"},
                }
            })

    client = FakeClient()
    monkeypatch.setattr(youtube_feed, "_get_platform_cookies", lambda _platform: {})
    monkeypatch.setattr(youtube_feed, "get_external_http_client", lambda: client)

    result = await youtube_feed.fetch_youtube_home_feed(limit=5)

    assert result["success"] is True
    assert result["feed_kind"] == "home"
    assert result["authenticated"] is False
    assert result["videos"][0]["url"] == "https://www.youtube.com/watch?v=video123"
    assert client.post_kwargs["json"]["browseId"] == "FEwhat_to_watch"
    assert "Authorization" not in client.post_kwargs["headers"]


@pytest.mark.asyncio
async def test_fetch_youtube_home_feed_falls_back_when_home_is_empty(monkeypatch):
    class FakeResponse:
        def __init__(self, *, text="", payload=None):
            self.text = text
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self):
            self.post_urls = []

        async def get(self, *_args, **_kwargs):
            return FakeResponse(text=(
                '<script>ytcfg.set({"INNERTUBE_API_KEY":"api-key",'
                '"INNERTUBE_CONTEXT":{"client":{"clientVersion":"1.2.3"}}});</script>'
            ))

        async def post(self, url, **_kwargs):
            self.post_urls.append(url)
            if url.endswith("/browse"):
                return FakeResponse(payload={"richGridRenderer": {"contents": []}})
            return FakeResponse(payload={
                "videoRenderer": {
                    "videoId": "fallback123",
                    "title": {"simpleText": "Public discovery video"},
                }
            })

    client = FakeClient()
    monkeypatch.setattr(youtube_feed, "_get_platform_cookies", lambda _platform: {})
    monkeypatch.setattr(youtube_feed, "get_external_http_client", lambda: client)

    result = await youtube_feed.fetch_youtube_home_feed(limit=5)

    assert result["success"] is True
    assert result["feed_kind"] == "public_discovery"
    assert result["videos"][0]["video_id"] == "fallback123"
    assert client.post_urls[-1].endswith("/search")


@pytest.mark.asyncio
async def test_fetch_youtube_home_feed_formats_empty_timeout_error(monkeypatch):
    class TimeoutClient:
        async def get(self, *_args, **_kwargs):
            raise httpx.ConnectTimeout("")

    monkeypatch.setattr(youtube_feed, "_get_platform_cookies", lambda _platform: {})
    monkeypatch.setattr(youtube_feed, "get_external_http_client", TimeoutClient)

    result = await youtube_feed.fetch_youtube_home_feed(limit=5)

    assert result["success"] is False
    assert result["error"].startswith("ConnectTimeout:")
    assert "代理" in result["error"]


@pytest.mark.asyncio
async def test_video_region_route_uses_youtube_outside_china(monkeypatch):
    async def fake_youtube(limit):
        return {"success": True, "source": "youtube", "videos": [{"title": str(limit)}]}

    monkeypatch.setattr(trending_content, "is_china_region", lambda: False)
    monkeypatch.setattr(trending_content, "fetch_youtube_home_feed", fake_youtube)

    result = await trending_content.fetch_video_content(limit=7)

    assert result["region"] == "non-china"
    assert result["video"]["source"] == "youtube"
    assert result["video"]["videos"][0]["title"] == "7"


@pytest.mark.asyncio
async def test_video_region_route_always_propagates_failure_error(monkeypatch):
    async def fake_youtube(_limit):
        return {"success": False, "source": "youtube", "videos": []}

    monkeypatch.setattr(trending_content, "is_china_region", lambda: False)
    monkeypatch.setattr(trending_content, "fetch_youtube_home_feed", fake_youtube)

    result = await trending_content.fetch_video_content(limit=7)

    assert result["success"] is False
    assert result["error"] == "youtube 获取失败（无错误详情）"


def test_youtube_video_format_and_source_links():
    raw = {
        "success": True,
        "region": "non-china",
        "video": {
            "success": True,
            "videos": [{
                "title": "A useful video",
                "author": "Creator",
                "view_count": "1K views",
                "url": "https://www.youtube.com/watch?v=abc",
                "source": "YouTube",
            }],
        },
    }

    formatted = trending_content.format_video_content(raw)
    links = _extract_links_from_raw("video", raw)

    assert "【YouTube 推荐】" in formatted
    assert "Creator | 1K views" in formatted
    assert links == [{
        "title": "A useful video",
        "url": "https://www.youtube.com/watch?v=abc",
        "source": "YouTube",
    }]


def test_youtube_cookie_validation_accepts_either_sapisid_variant():
    assert cookies_login.validate_cookies("youtube", {"SAPISID": "a"}) is True
    assert cookies_login.validate_cookies("youtube", {"__Secure-3PAPISID": "b"}) is True
    assert cookies_login.validate_cookies("youtube", {"SID": "c"}) is False
