"""Read-only discovery and authoritative duration/danmaku checks."""
import asyncio
import math
from urllib.parse import urlparse

import httpx

from .engine import MAX_SECONDS, parse_video_url


def eligible(duration, danmaku):
    try:
        seconds, count = float(duration), float(danmaku)
        return (math.isfinite(seconds) and math.isfinite(count)
                and 0 < seconds < 180 and count * 60 > seconds * 100)
    except (TypeError, ValueError):
        return False


async def inspect_video(url):
    from bilibili_api import Credential, video
    from utils.web_scraper.platform_helpers import _get_bilibili_credential
    url = url.strip()
    if urlparse(url).hostname == "b23.tv":
        if urlparse(url).scheme not in ("https", "http"):
            raise ValueError("Invalid video URL")
        async with httpx.AsyncClient(follow_redirects=False, timeout=15) as client:
            response = await client.get(url)
            url = response.headers.get("location", "")
    bvid, page = parse_video_url(url)
    credential = await asyncio.to_thread(_get_bilibili_credential)
    info = await asyncio.wait_for(video.Video(bvid=bvid, credential=credential or Credential()).get_info(), 40)
    pages = info.get("pages", [])
    if page >= len(pages):
        raise ValueError("Video part unavailable")
    seconds = float(pages[page]["duration"])
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS:
        raise ValueError("Video must be no longer than 20 minutes")
    count = info.get("stat", {}).get("danmaku")
    return {"url": f"https://www.bilibili.com/video/{bvid}?p={page + 1}",
            "title": info["title"], "duration": seconds, "danmaku": count,
            "parts": len(pages), "danmaku_per_minute": count * 60 / seconds if isinstance(count, (int, float)) else None}


def enforce_policy(info, *, automatic=False, confirmed_duration=None):
    if automatic:
        if info["parts"] != 1 or not eligible(info["duration"], info["danmaku"]):
            raise ValueError("Automatic selection requires a single-part video under 3 minutes and over 100 danmaku/minute")
    elif info["duration"] > 300 and confirmed_duration != info["duration"]:
        return False
    return True


async def discover(topic, exclude=()):
    from bilibili_api import hot, search
    seen = set(exclude)
    # Bounded search; never weaken the constraints when the result set is empty.
    for page in range(1, 4):
        if topic:
            result = await asyncio.wait_for(search.search_by_type(
                topic, search_type=search.SearchObjectType.VIDEO,
                order_type=search.OrderVideo.CLICK, time_range=10, page=page), 40)
            rows = result.get("result", [])
        else:
            result = await asyncio.wait_for(hot.get_hot_videos(pn=page, ps=20), 40)
            rows = result.get("list", [])
        for row in rows:
            bvid = row.get("bvid")
            if not bvid or bvid in seen:
                continue
            seen.add(bvid)
            seconds = row.get("duration")
            if isinstance(seconds, str) and ":" in seconds:
                try:
                    seconds = sum(float(value) * 60 ** index for index, value in enumerate(reversed(seconds.split(":"))))
                except ValueError:
                    continue
            count = row.get("video_review", row.get("stat", {}).get("danmaku"))
            if not eligible(seconds, count):
                continue
            try:
                info = await inspect_video(bvid)
                enforce_policy(info, automatic=True)
            except ValueError:
                continue
            return {"video": info, "topic": topic}
    return {"video": None, "topic": topic}
