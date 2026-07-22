# -*- coding: utf-8 -*-
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

"""Framework-independent music recommendation behavior for proactive chat."""

from collections.abc import Callable
from dataclasses import dataclass

from config.prompts.prompts_proactive import (
    MUSIC_SEARCH_RESULT_TEXTS,
    PROACTIVE_MUSIC_TAG_INSTRUCTIONS,
    get_proactive_music_failsafe_hint,
    get_proactive_music_playing_hint,
    get_proactive_music_strict_constraint,
    get_proactive_music_unknown_track_name,
)
from utils.logger_config import get_module_logger
from utils.music_crawlers import fetch_music_content

from .state import _clear_channel_from_proactive_history

logger = get_module_logger(__name__, "Main")


@dataclass(frozen=True)
class MusicRecommendationSelection:
    """A playable music candidate and its prompt-facing representation."""

    content: dict | None = None
    topic: str = ""
    link: dict | None = None
    topic_key: str = ""


def _log_music_content(lanlan_name: str, music_content: dict) -> None:
    """Log the result of a music recommendation fetch."""
    if music_content.get("success"):
        tracks = music_content.get("data", [])
        titles = [
            f"{track.get('name', '')} - {track.get('artist', '')}"
            for track in tracks[:5]
        ]
        if titles:
            logger.debug("[%s] 成功获取音乐推荐:", lanlan_name)
            for title in titles:
                logger.debug("  - %s", title)
        return

    logger.warning(
        "[%s] 音乐获取失败: %s",
        lanlan_name,
        music_content.get("error", "未知错误"),
    )


def _format_music_content(music_content: dict, lang: str = "zh") -> str:
    """Format music search results for the proactive-generation prompt."""
    if not music_content.get("success"):
        return ""

    text = MUSIC_SEARCH_RESULT_TEXTS.get(lang, MUSIC_SEARCH_RESULT_TEXTS["en"])
    output_lines = [text["title"]]
    for index, track in enumerate(music_content.get("data", [])[:5], 1):
        name = track.get("name") or text["unknown_track"]
        artist = track.get("artist") or text["unknown_artist"]
        album = track.get("album", "")
        if album:
            output_lines.append(
                f"{index}. 《{name}》 - {artist}（{text['album']}：{album}）"
            )
        else:
            output_lines.append(f"{index}. 《{name}》 - {artist}")

    return "\n".join(output_lines) if len(output_lines) > 1 else ""


def _append_music_recommendations(
    source_links: list[dict],
    music_content: dict | None,
    limit: int = 3,
) -> int:
    """Append unique recommendation links and return the number added."""
    music_raw = music_content.get("raw_data", {}) if music_content else {}
    tracks = music_raw.get("data")
    if not tracks:
        return 0

    existing_signatures = {
        (
            (link.get("url") or "").strip(),
            (link.get("title") or "").strip(),
            (link.get("artist") or "").strip(),
        )
        for link in source_links
        if isinstance(link, dict) and link.get("source") == "音乐推荐"
    }

    appended = 0
    for track in tracks[:limit]:
        title = (track.get("name") or "未知曲目").strip()
        artist = (track.get("artist") or "未知艺术家").strip()
        url = (track.get("url") or "").strip()
        signature = (url, title, artist)
        if signature in existing_signatures:
            continue
        source_links.append(
            {
                "title": title,
                "artist": artist,
                "url": url,
                "cover": track.get("cover", ""),
                "source": "音乐推荐",
            }
        )
        existing_signatures.add(signature)
        appended += 1
    return appended


def _select_music_recommendation(
    music_content: dict | None,
    *,
    lang: str,
    source_hash: Callable[[str, str], str],
    should_skip_source: Callable[[str], bool],
    lanlan_name: str = "",
) -> MusicRecommendationSelection:
    """Select the first non-suppressed track and keep prompt/link data aligned."""
    if not music_content or not music_content.get("formatted_content"):
        return MusicRecommendationSelection()

    tracks = music_content.get("raw_data", {}).get("data", [])
    if not tracks:
        logger.debug(
            "[%s] 音乐 formatted_content 非空但无曲目数据，跳过音乐通道",
            lanlan_name,
        )
        return MusicRecommendationSelection()

    picked_index = -1
    picked_key = ""
    for index, candidate in enumerate(tracks):
        name = candidate.get("name", "")
        key = source_hash(
            candidate.get("url", ""),
            f"{name} - {candidate.get('artist', '')}",
        )
        if key and should_skip_source(key):
            logger.debug("[%s] 音乐候选去重命中，跳过: %s", lanlan_name, name)
            continue
        picked_index = index
        picked_key = key
        break

    if picked_index < 0:
        logger.debug("[%s] 所有音乐候选均被衰减 skip，清空音乐通道", lanlan_name)
        return MusicRecommendationSelection()

    selected_content = music_content
    topic = music_content["formatted_content"]
    if picked_index:
        raw_data = music_content.get("raw_data") or {}
        trimmed_raw_data = {**raw_data, "data": tracks[picked_index:]}
        trimmed_topic = _format_music_content(trimmed_raw_data, lang)
        if trimmed_topic:
            topic = trimmed_topic
            selected_content = {
                **music_content,
                "formatted_content": topic,
                "raw_data": trimmed_raw_data,
            }

    track = tracks[picked_index]
    link = {
        "title": track.get("name", ""),
        "artist": track.get("artist", ""),
        "url": track.get("url", ""),
        "cover": track.get("cover", ""),
        "source": "音乐推荐",
        "type": "music",
    }
    return MusicRecommendationSelection(
        content=selected_content,
        topic=topic,
        link=link,
        topic_key=picked_key,
    )


def _build_music_playing_hint(
    *,
    is_playing_music: bool,
    current_track: dict | None,
    master_name: str,
    lang: str,
) -> str:
    """Build the current-playback hint without exposing prompt storage details."""
    if not is_playing_music or not current_track:
        return ""
    track_name = current_track.get("name") or get_proactive_music_unknown_track_name(
        lang
    )
    return get_proactive_music_playing_hint(track_name, master_name, lang)


def _build_music_dynamic_context(
    *,
    selected_music_link: dict | None,
    music_content: dict | None,
    is_playing_music: bool,
    master_name: str,
    lang: str,
) -> str:
    """Build recommendation tag/failsafe and active-playback constraints."""
    context = ""
    if selected_music_link:
        context += PROACTIVE_MUSIC_TAG_INSTRUCTIONS.get(
            lang,
            PROACTIVE_MUSIC_TAG_INSTRUCTIONS.get(
                "en", PROACTIVE_MUSIC_TAG_INSTRUCTIONS["zh"]
            ),
        )
        raw_data = music_content.get("raw_data", {}) if music_content else {}
        if raw_data.get("best_match", {}).get("status") == "fuzzy":
            context += get_proactive_music_failsafe_hint(master_name, lang)

    if is_playing_music:
        context += get_proactive_music_strict_constraint(lang)
    return context


async def _fetch_music_with_fallback(
    keyword: str,
    *,
    lanlan_name: str = "",
) -> dict | None:
    """Search by keyword, falling back to a random recommendation."""
    try:
        result = await fetch_music_content(keyword=keyword, limit=5)
        if result and result.get("success"):
            return result
    except Exception as exc:
        logger.warning("[%s] 音乐关键词 %r 搜索异常: %s", lanlan_name, keyword, exc)

    logger.warning("[%s] 音乐关键词 %r 搜索失败，尝试随机推荐", lanlan_name, keyword)
    try:
        return await fetch_music_content(keyword="", limit=5)
    except Exception as exc:
        logger.warning("[%s] 随机音乐推荐异常: %s", lanlan_name, exc)
        return None


def _record_music_played_through(lanlan_name: str) -> int:
    """Apply completed-playback feedback to music-channel decay history."""
    cleared = _clear_channel_from_proactive_history(lanlan_name, "music")
    if cleared:
        logger.info(
            "[%s] 音乐完整播放，重置 music 通道权重衰减（清空 %s 条）",
            lanlan_name,
            cleared,
        )
    return cleared
