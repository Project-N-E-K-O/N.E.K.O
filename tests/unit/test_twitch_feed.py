from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils import twitch_auth
from utils.web_scraper import trending_content, twitch_feed


@pytest.mark.asyncio
async def test_twitch_live_streams_use_encrypted_credential_and_public_projection(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{
                "id": "stream-123", "user_login": "streamer", "user_name": "Streamer", "title": "Ranked matches",
                "game_name": "VALORANT", "viewer_count": 1234,
            }]}

    class _Client:
        async def get(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return _Response()

    async def _credential(*, force_refresh=False):
        assert force_refresh is False
        return "clientid123", "secret-token", "42"

    monkeypatch.setattr(twitch_feed._auth_service, "followed_stream_access", _credential)
    monkeypatch.setattr(twitch_feed, "get_external_http_client", lambda: _Client())

    result = await twitch_feed.fetch_twitch_live_streams(limit=5)

    assert result == {
        "success": True,
        "source": "twitch",
        "videos": [{
            "stream_id": "stream-123",
            "title": "Ranked matches",
            "author": "Streamer",
            "url": "https://www.twitch.tv/streamer",
            "source": "Twitch",
            "game_name": "VALORANT",
            "viewer_count": "1234",
        }],
    }
    assert captured["params"] == {"user_id": "42", "first": 5}
    assert captured["headers"]["Client-ID"] == "clientid123"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in str(result)

    repeated = await twitch_feed.fetch_twitch_live_streams(limit=5)
    assert repeated == result


@pytest.mark.asyncio
async def test_twitch_device_exchange_sends_scopes_and_saves_only_after_validation(monkeypatch):
    requests = []

    async def _request(method, url, *, headers=None, data=None):
        requests.append((method, url, headers, data))
        if url.endswith("/device"):
            return 200, {
                "device_code": "device-code", "user_code": "ABCD-EFGH",
                "verification_uri": "https://www.twitch.tv/activate", "expires_in": 900, "interval": 5,
            }
        if url.endswith("/token"):
            return 200, {"access_token": "access-secret", "refresh_token": "refresh-secret"}
        return 200, {
            "client_id": "clientid123", "user_id": "42", "login": "neko_user",
            "scopes": ["user:read:follows"], "expires_in": 3600,
        }

    saved = []
    async def _save(credential):
        saved.append(credential)
        return True

    monkeypatch.setattr(twitch_auth, "_request", _request)
    monkeypatch.setattr(twitch_auth, "_save", _save)
    service = twitch_auth.TwitchAuthService()

    started = await service.start("clientid123")
    checked = await service.check_device_code("clientid123")

    assert started["user_code"] == "ABCD-EFGH"
    assert checked["logged_in"] is True
    device_request = next(data for _, url, _, data in requests if url.endswith("/device"))
    token_request = next(data for _, url, _, data in requests if url.endswith("/token"))
    assert device_request["scopes"] == "user:read:follows"
    assert token_request["scopes"] == "user:read:follows"
    assert saved and saved[0]["access_token"] == "access-secret"
    assert "access-secret" not in str(checked)


@pytest.mark.asyncio
async def test_twitch_status_reports_saved_follow_credential(monkeypatch):
    async def _load():
        return {
            "client_id": "clientid123",
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "user_id": "42",
            "login": "neko_user",
            "scopes": "user:read:follows",
            "expires_at": "2000000000",
        }

    monkeypatch.setattr(twitch_auth, "_load", _load)

    result = await twitch_auth.TwitchAuthService().status()

    assert result["logged_in"] is True
    assert result["has_cookies"] is True
    assert result["login"] == "neko_user"
    assert "access-secret" not in str(result)


def test_twitch_media_credential_ui_is_localized_in_every_locale():
    for locale_path in Path("static/locales").glob("*.json"):
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        section = payload["cookiesLogin"]
        assert section["twitch"]
        assert section["instructions"]["twitch"]
        assert section["twitchAuth"]["start"]


@pytest.mark.asyncio
async def test_non_china_video_source_combines_twitch_and_youtube(monkeypatch):
    async def _twitch(limit):
        return {"success": True, "source": "twitch", "videos": [
            {"title": f"Live {limit}-1", "author": "Streamer"},
            {"title": f"Live {limit}-2", "author": "Streamer"},
        ]}

    async def _youtube(limit):
        return {"success": True, "source": "youtube", "videos": [
            {"title": f"Video {limit}-1"},
            {"title": f"Video {limit}-2"},
        ]}

    monkeypatch.setattr(trending_content, "is_china_region", lambda: False)
    monkeypatch.setattr(trending_content, "fetch_twitch_live_streams", _twitch)
    monkeypatch.setattr(trending_content, "fetch_youtube_home_feed", _youtube)

    result = await trending_content.fetch_video_content(limit=7)

    assert result["success"] is True
    assert result["video"]["source"] == "mixed"
    assert [item["title"] for item in result["video"]["videos"]] == [
        "Live 7-1", "Video 7-1", "Live 7-2", "Video 7-2",
    ]
    formatted = trending_content.format_video_content(result)
    assert "[Followed Twitch live streams]" in formatted
    assert "【YouTube 推荐】" in formatted


@pytest.mark.asyncio
async def test_non_china_video_source_falls_back_to_youtube_when_twitch_is_unavailable(monkeypatch):
    async def _twitch(_limit):
        return {"success": False, "source": "twitch", "videos": [], "error": "not configured"}

    async def _youtube(limit):
        return {"success": True, "source": "youtube", "videos": [{"title": f"Video {limit}"}]}

    monkeypatch.setattr(trending_content, "is_china_region", lambda: False)
    monkeypatch.setattr(trending_content, "fetch_twitch_live_streams", _twitch)
    monkeypatch.setattr(trending_content, "fetch_youtube_home_feed", _youtube)

    result = await trending_content.fetch_video_content(limit=3)

    assert result["success"] is True
    assert result["video"]["source"] == "mixed"
    assert result["youtube"]["videos"][0]["title"] == "Video 3"
