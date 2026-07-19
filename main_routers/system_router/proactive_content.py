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

"""Formatters / file loggers for proactive content payloads (news,
video, trending, music, personal dynamics).

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import logger
from config.prompts.prompts_proactive import (
    MUSIC_SEARCH_RESULT_TEXTS,
)


def _tieba_log_title(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("title", "topic_name", "word"):
        title = str(item.get(key, "") or "").strip()
        if title:
            return title
    return ""


def _log_news_content(lanlan_name: str, news_content: dict):
    """
    Log news content fetch details.
    """
    region = news_content.get('region', 'china')
    news_data = news_content.get('news', {})
    if news_data.get('success'):
        trending_list = news_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            source = "微博热议话题" if region == 'china' else "Twitter热门话题"
            print(f"[{lanlan_name}] 成功获取{source}:")
            for word in words:
                print(f"  - {word}")
    xhh_data = news_content.get('xhh') or {}
    if xhh_data.get('success'):
        posts = xhh_data.get('posts') or []
        titles = [post.get('title', '') for post in posts[:5]]
        if titles:
            print(f"[{lanlan_name}] 成功获取小黑盒首页内容:")
            for title in titles:
                print(f"  - {title}")

    tieba_data = news_content.get('tieba', {}) or {}
    if tieba_data.get('success'):
        posts = tieba_data.get('posts', []) or (tieba_data.get('tieba', {}) or {}).get('posts', [])
        topics = tieba_data.get('topics', []) or (tieba_data.get('tieba', {}) or {}).get('topics', [])
        tieba_items = list(posts or []) + list(topics or [])
        titles = [title for item in tieba_items if (title := _tieba_log_title(item))][:5]
        if titles:
            print(f"[{lanlan_name}] 成功获取贴吧资源池: {len(tieba_items)} 条")
            for title in titles:
                print(f"  - {title}")


def _log_video_content(lanlan_name: str, video_content: dict):
    """
    Log video content fetch details.
    """
    region = video_content.get('region', 'china')
    video_data = video_content.get('video', {})
    if video_data.get('success'):
        videos = video_data.get('videos', [])
        titles = [video.get('title', '') for video in videos[:5]]
        if titles:
            source = "B站视频" if region == 'china' else (
                "Twitch 直播分类" if video_data.get("source") == "twitch" else "YouTube视频"
            )
            print(f"[{lanlan_name}] 成功获取{source}:")
            for title in titles:
                print(f"  - {title}")


def _log_trending_content(lanlan_name: str, trending_content: dict):
    """
    Log homepage recommendation content fetch details.
    """
    content_details = []
    
    bilibili_data = trending_content.get('bilibili', {})
    if bilibili_data.get('success'):
        videos = bilibili_data.get('videos', [])
        titles = [video.get('title', '') for video in videos[:5]]
        if titles:
            content_details.append("B站视频:")
            for title in titles:
                content_details.append(f"  - {title}")
    
    weibo_data = trending_content.get('weibo', {})
    if weibo_data.get('success'):
        trending_list = weibo_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            content_details.append("微博话题:")
            for word in words:
                content_details.append(f"  - {word}")
    
    reddit_data = trending_content.get('reddit', {})
    if reddit_data.get('success'):
        posts = reddit_data.get('posts', [])
        titles = [post.get('title', '') for post in posts[:5]]
        if titles:
            content_details.append("Reddit热门帖子:")
            for title in titles:
                content_details.append(f"  - {title}")
    
    twitter_data = trending_content.get('twitter', {})
    if twitter_data.get('success'):
        trending_list = twitter_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            content_details.append("Twitter热门话题:")
            for word in words:
                content_details.append(f"  - {word}")
    
    if content_details:
        print(f"[{lanlan_name}] 成功获取首页推荐:")
        for detail in content_details:
            print(detail)
    else:
        print(f"[{lanlan_name}] 成功获取首页推荐 - 但未获取到具体内容")


def _log_music_content(lanlan_name: str, music_content: dict):
    """Log music content fetch details."""
    if music_content.get('success'):
        tracks = music_content.get('data', [])
        titles = [f"{t.get('name', '')} - {t.get('artist', '')}" for t in tracks[:5]]
        if titles:
            logger.debug(f"[{lanlan_name}] 成功获取音乐推荐:")
            for title in titles:
                logger.debug(f"  - {title}")
    else:
        logger.warning(f"[{lanlan_name}] 音乐获取失败: {music_content.get('error', '未知错误')}")


def _format_music_content(music_content: dict, lang: str = 'zh') -> str:
    """Formats music content into a readable string with multi-language support."""
    if not music_content.get('success'):
        return ""
    
    t = MUSIC_SEARCH_RESULT_TEXTS.get(lang, MUSIC_SEARCH_RESULT_TEXTS['en'])
    
    output_lines = [t['title']]
    tracks = music_content.get('data', [])
    for i, track in enumerate(tracks[:5], 1):
        # 使用多语言字典中的"未知"占位符，替代硬编码的中文
        name = track.get('name') or t['unknown_track']
        artist = track.get('artist') or t['unknown_artist']
        album = track.get('album', '')
        
        if album:
            output_lines.append(f"{i}. 《{name}》 - {artist}（{t['album']}：{album}）")
        else:
            output_lines.append(f"{i}. 《{name}》 - {artist}")
    
    # 如果除了标题没有抓到任何歌曲，则返回空
    if len(output_lines) == 1:
        return ""
        
    # 删除了原来的 desc 尾注，保持素材的客观中立
    return "\n".join(output_lines)


def _append_music_recommendations(
    source_links: list[dict],
    music_content: dict | None,
    limit: int = 3,
) -> int:
    """Deduplicate and append music tracks from *music_content* into *source_links*.

    Returns the number of tracks actually appended (0 when nothing new).
    """
    music_raw = music_content.get('raw_data', {}) if music_content else {}
    tracks = music_raw.get('data')
    if not tracks:
        return 0

    existing_signatures = {
        (
            (link.get('url') or '').strip(),
            (link.get('title') or '').strip(),
            (link.get('artist') or '').strip(),
        )
        for link in source_links
        if isinstance(link, dict) and link.get('source') == '音乐推荐'
    }

    appended = 0
    for track in tracks[:limit]:
        title = (track.get('name') or '未知曲目').strip()
        artist = (track.get('artist') or '未知艺术家').strip()
        url = (track.get('url') or '').strip()
        sig = (url, title, artist)
        if sig in existing_signatures:
            continue
        source_links.append({
            'title': title,
            'artist': artist,
            'url': url,
            'cover': track.get('cover', ''),
            'source': '音乐推荐',
        })
        existing_signatures.add(sig)
        appended += 1
    return appended


def _log_personal_dynamics(lanlan_name: str, personal_content: dict):
    """
    Log personal feed content fetch details.
    """
    content_details = []
    
    bilibili_dynamic = personal_content.get('bilibili_dynamic', {})
    if bilibili_dynamic.get('success'):
        dynamics = bilibili_dynamic.get('dynamics', [])
        bilibili_contents = [dynamic.get('content', dynamic.get('title', '')) for dynamic in dynamics[:5]]
        if bilibili_contents:
            content_details.append("B站动态:")
            for content in bilibili_contents:
                content_details.append(f"  - {content}")
    
    weibo_dynamic = personal_content.get('weibo_dynamic', {})
    if weibo_dynamic.get('success'):
        dynamics = weibo_dynamic.get('statuses', [])
        weibo_contents = [dynamic.get('content', '') for dynamic in dynamics[:5]]
        if weibo_contents:
            content_details.append("微博动态:")
            for content in weibo_contents:
                content_details.append(f"  - {content}")
                
    if content_details:
        print(f"[{lanlan_name}] 成功获取个人动态:")
        for detail in content_details:
            print(detail)
    else:
        print(f"[{lanlan_name}] 成功获取个人动态 - 但未获取到具体内容")
