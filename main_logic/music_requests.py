# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Controlled music directives and resolution for proactive recommendations."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

MusicFetcher = Callable[..., Awaitable[dict[str, Any]]]
_RECENT_QUERY_TTL_SECONDS = 300.0
_RECENT_QUERY_LIMIT_PER_SCOPE = 20
_recent_music_queries: dict[tuple[str, str], float] = {}


@dataclass(frozen=True)
class MusicRequest:
    keyword: str = ""
    song_name: str = ""
    song_artist: str = ""
    playlist_name: str = ""
    personalization_source: str = "auto"

    @property
    def strict(self) -> bool:
        return bool(
            self.song_name
            or self.song_artist
            or self.playlist_name
            or self.personalization_source != "auto"
        )


def _music_request_query_key(request: MusicRequest) -> str:
    if request.playlist_name:
        value = f"playlist:{request.playlist_name}"
    elif request.personalization_source != "auto":
        value = f"source:{request.personalization_source}"
    elif not request.keyword:
        value = "source:auto"
    else:
        value = request.keyword
    return " ".join(value.casefold().split())


def was_music_request_recent(scope: str, request: MusicRequest) -> bool:
    key = _music_request_query_key(request)
    if not key:
        return False
    timestamp = _recent_music_queries.get((scope, key))
    return (
        timestamp is not None
        and time.monotonic() - timestamp < _RECENT_QUERY_TTL_SECONDS
    )


def mark_music_request_query(scope: str, request: MusicRequest) -> None:
    key = _music_request_query_key(request)
    if not key:
        return
    now = time.monotonic()
    scope_items = [
        (cache_key, timestamp)
        for cache_key, timestamp in _recent_music_queries.items()
        if cache_key[0] == scope
    ]
    for cache_key, timestamp in scope_items:
        if now - timestamp >= _RECENT_QUERY_TTL_SECONDS:
            _recent_music_queries.pop(cache_key, None)
    scope_items = [item for item in scope_items if item[0] in _recent_music_queries]
    if len(scope_items) >= _RECENT_QUERY_LIMIT_PER_SCOPE:
        _recent_music_queries.pop(min(scope_items, key=lambda item: item[1])[0], None)
    _recent_music_queries[(scope, key)] = now


def parse_music_request(value: str) -> MusicRequest:
    """Parse the controlled directives emitted by proactive chat."""
    normalized = str(value or "").strip()
    for prefix in ("playlist:", "playlist：", "歌单:", "歌单："):
        if normalized.casefold().startswith(prefix.casefold()):
            name = normalized[len(prefix) :].strip(" '\"「」『』《》")
            return MusicRequest(playlist_name=name)

    for prefix in ("song:", "song：", "歌曲:", "歌曲："):
        if normalized.casefold().startswith(prefix.casefold()):
            payload = normalized[len(prefix) :].strip(" '\"「」『』《》")
            song_name, separator, song_artist = payload.partition("|")
            song_name = song_name.strip(" '\"「」『』《》")
            song_artist = (
                song_artist.strip(" '\"「」『』《》") if separator else ""
            )
            return MusicRequest(
                keyword=" ".join(
                    part for part in (song_name, song_artist) if part
                ),
                song_name=song_name,
                song_artist=song_artist,
            )

    for prefix in ("source:", "source："):
        if normalized.casefold().startswith(prefix.casefold()):
            source = normalized[len(prefix) :].strip().casefold()
            aliases = {
                "liked": "liked",
                "favorites": "liked",
                "我喜欢": "liked",
                "红心": "liked",
                "daily": "daily",
                "daily recommendations": "daily",
                "日推": "daily",
                "每日推荐": "daily",
            }
            normalized_source = aliases.get(source)
            if normalized_source:
                return MusicRequest(personalization_source=normalized_source)
            logger.warning("未知音乐来源指令: %r", source)
            return MusicRequest()

    if normalized.casefold() in {"personalized", "个性化", "按喜好推荐"}:
        return MusicRequest()
    return MusicRequest(keyword=normalized)


async def fetch_music_request(
    request: MusicRequest,
    *,
    limit: int = 5,
    source_locale: str | None = None,
    fetcher: MusicFetcher | None = None,
    allow_keyword_fallback: bool = False,
    include_failure: bool = False,
    bypass_recommendation_dedupe: bool = False,
) -> dict[str, Any] | None:
    """Resolve a controlled request without widening strict directives."""
    if fetcher is None:
        from utils.music_crawlers import fetch_music_content

        fetcher = fetch_music_content

    async def fetch(keyword: str) -> dict[str, Any]:
        try:
            return await fetcher(
                keyword=keyword,
                limit=limit,
                source_locale=source_locale,
                personalized=True,
                playlist_name=request.playlist_name,
                personalization_source=request.personalization_source,
                requested_song=request.song_name,
                requested_artist=request.song_artist,
                bypass_recommendation_dedupe=bypass_recommendation_dedupe,
            )
        except Exception as exc:
            logger.warning("音乐请求获取失败: %s", exc)
            return {
                "success": False,
                "error_code": "upstream_error",
                "error": "Music provider request failed",
                "data": [],
            }

    result = await fetch(request.keyword)
    if result and result.get("success"):
        return result
    if request.strict or not request.keyword or not allow_keyword_fallback:
        return result if include_failure else None

    fallback = await fetch("")
    if fallback and fallback.get("success"):
        return fallback
    return fallback if include_failure else None
