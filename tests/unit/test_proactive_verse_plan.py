import json
from unittest.mock import AsyncMock

import httpx
import pytest

from main_logic.proactive_chat import generation, service, verse_plan
from main_logic.proactive_chat.contracts import ProactiveChatCommand
from utils.proactive_preferences.personalization import RecommendationContext


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,choices", [(200, ["video", "news"]), (200, []), (404, [])]
)
async def test_verse_plan_replaces_public_collection_but_keeps_local_sources(
    monkeypatch, status, choices
):
    origin = "https://verse.example.test"
    requests = []
    materials = {
        "video": {
            "mode": "video",
            "title": "Verse video",
            "url": "https://www.youtube.com/watch?v=01234567890",
            "source": "YouTube",
        },
        "news": {
            "mode": "news",
            "title": "Verse headline",
            "url": "https://news.google.com/rss/articles/test",
            "source": "Google News",
        },
    }

    def respond(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={"version": 1, "choices": [materials[mode] for mode in choices]},
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(verse_plan, "social_base_url", lambda: origin)
    monkeypatch.setattr(
        verse_plan.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    monkeypatch.setattr(verse_plan, "_should_skip_source", lambda key: False)
    monkeypatch.setattr(service, "is_china_region", lambda: False)
    local = AsyncMock(return_value={"personal": {"links": []}})
    monkeypatch.setattr(service, "collect_proactive_sources", local)
    modes = ["news", "music", "video", "personal"]
    _, found, _, cloud = await service._collect_sources_under_vision_consent(
        command=ProactiveChatCommand(language="en"),
        enabled_modes=modes,
        lanlan_name="NEKO",
        vision_consent_check=lambda: True,
        music_available=False,
        recommendations=RecommendationContext(
            community=origin,
            bearer="test-token",
            account_id="test-user",
        ),
    )

    assert cloud == (status == 200)
    assert local.await_args.kwargs["enabled_modes"] == (
        ["personal"] if cloud else modes
    )
    assert set(found) == ({"video", "personal"} if choices else {"personal"})
    assert requests[0].url.path == "/api/proactive/recommendations"
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert json.loads(requests[0].content) == {
        "version": 1,
        "account_id": "test-user",
        "modes": ["news", "video"],
        "locale": "en",
        "china": False,
    }


@pytest.mark.asyncio
async def test_music_uses_verse_keyword_and_can_still_pass(monkeypatch):
    get_music = AsyncMock(return_value=None)
    monkeypatch.setattr(generation, "_fetch_music_with_fallback", get_music)
    options = dict(
        has_music_task=True,
        has_meme_task=False,
        proactive_lang="zh",
        lanlan_name="NEKO",
        music_content={"placeholder": True, "verse_keyword": "personalized"},
        meme_content=None,
    )
    await generation._fetch_phase1_followups(
        parsed={"music_keyword": "another artist"}, **options
    )
    get_music.assert_awaited_once_with("personalized", lanlan_name="NEKO")
    await generation._fetch_phase1_followups(parsed={"music_pass": True}, **options)
    assert get_music.await_count == 1


@pytest.mark.asyncio
async def test_verse_meme_uses_the_selected_image_without_local_search(monkeypatch):
    get_memes = AsyncMock(return_value=None)
    monkeypatch.setattr(generation, "_fetch_meme_with_fallback", get_memes)
    chosen = {
        "title": "Surprised Cat",
        "url": "https://i.imgflip.com/example.jpg",
        "source": "Imgflip",
    }
    options = dict(
        has_music_task=False,
        has_meme_task=True,
        proactive_lang="en",
        lanlan_name="NEKO",
        music_content=None,
        meme_content={"placeholder": True, "verse_meme": chosen},
    )
    _, accepted = await generation._fetch_phase1_followups(
        parsed={"meme_keyword": "another image"}, **options
    )
    assert accepted["data"] == [chosen]
    _, passed = await generation._fetch_phase1_followups(
        parsed={"meme_pass": True}, **options
    )
    assert not passed.get("success")
    get_memes.assert_not_awaited()
