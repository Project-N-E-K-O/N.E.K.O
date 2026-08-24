"""Unit tests for AnySearch routing in the built-in web search plugin."""

import asyncio
import json

import httpx

from plugin.plugins.web_search import (
    _ANYSEARCH_SEARCH_URL,
    _fallback_backend,
    _search_anysearch,
    _select_anysearch_zone,
    _select_backend,
)


def test_auto_prefers_anysearch_and_maps_geoip_to_zone() -> None:
    assert _select_backend("auto", "CN") == "anysearch"
    assert _select_backend("auto", "US") == "anysearch"
    assert _select_anysearch_zone("CN") == "cn"
    assert _select_anysearch_zone("US") == "intl"
    assert _select_anysearch_zone(None) is None


def test_automatic_anysearch_fallback_matches_geoip() -> None:
    assert _fallback_backend("anysearch", "CN") == "baidu"
    assert _fallback_backend("anysearch", None) == "baidu"
    assert _fallback_backend("anysearch", "JP") == "duckduckgo"
    assert _fallback_backend("duckduckgo", "US") is None


def test_anysearch_request_uses_optional_auth_and_zone() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "results": [
                        {
                            "title": "AnySearch result",
                            "url": "https://example.com/result",
                            "snippet": "A result snippet.",
                        }
                    ]
                },
            },
        )

    async def run() -> list[dict[str, str]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _search_anysearch(
                client,
                "test query",
                max_results=50,
                zone="intl",
                api_key="test-key",
            )

    results = asyncio.run(run())
    assert seen["url"] == _ANYSEARCH_SEARCH_URL
    assert seen["payload"] == {"query": "test query", "max_results": 20, "zone": "intl"}
    assert seen["headers"]["authorization"] == "Bearer test-key"
    assert results == [{"title": "AnySearch result", "url": "https://example.com/result", "snippet": "A result snippet."}]


def test_anysearch_sanitizes_and_caps_results_to_requested_limit() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "results": [
                        {
                            "title": "unsafe\u202etitle",
                            "url": "javascript:alert(1)",
                            "snippet": "ignored",
                        },
                        *[
                            {
                                "title": f"result {index}\u200b" + ("x" * 240),
                                "url": f"https://example.com/{index}",
                                "snippet": "\u0000" + ("y" * 400),
                            }
                            for index in range(6)
                        ],
                    ]
                },
            },
        )

    async def run() -> list[dict[str, str]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _search_anysearch(client, "test query", max_results=3)

    results = asyncio.run(run())
    assert len(results) == 3
    assert all(item["url"].startswith("https://") for item in results)
    assert all("\u200b" not in item["title"] for item in results)
    assert all("\u0000" not in item["snippet"] for item in results)
    # Existing parser clipping appends an ellipsis after the configured limit.
    assert all(len(item["title"]) <= 201 for item in results)
    assert all(len(item["snippet"]) <= 321 for item in results)
