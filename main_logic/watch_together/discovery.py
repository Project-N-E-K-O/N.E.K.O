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
    except (TypeError, ValueError, OverflowError):
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
            location = response.headers.get("location", "").strip()
            if response.status_code not in (301, 302, 303, 307, 308) or not location:
                raise httpx.HTTPStatusError("Short-link metadata unavailable", request=response.request, response=response)
            url = str(response.url.join(location))
            try:
                parse_video_url(url)
            except ValueError as exc:
                raise httpx.HTTPStatusError("Invalid short-link destination", request=response.request, response=response) from exc
    bvid, page = parse_video_url(url)
    credential = await asyncio.to_thread(_get_bilibili_credential)
    info = await asyncio.wait_for(video.Video(bvid=bvid, credential=credential or Credential()).get_info(), 40)
    pages = info.get("pages", [])
    if page >= len(pages):
        raise ValueError("Video part unavailable")
    try:
        seconds = float(pages[page]["duration"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid video duration") from exc
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS:
        raise ValueError("Video must be no longer than 20 minutes")
    count = info.get("stat", {}).get("danmaku")
    try:
        count = float(count) if not isinstance(count, bool) else None
        if count is not None and (not math.isfinite(count) or count < 0 or not math.isfinite(count / seconds * 60)):
            count = None
    except (TypeError, ValueError, OverflowError):
        count = None
    return {"url": f"https://www.bilibili.com/video/{bvid}?p={page + 1}",
            "title": info["title"], "duration": seconds, "danmaku": count,
            "parts": len(pages), "danmaku_per_minute": count / seconds * 60 if count is not None else None}


def enforce_policy(info, *, automatic=False, confirmed_duration=None):
    if automatic:
        if info["parts"] != 1 or not eligible(info["duration"], info["danmaku"]):
            raise ValueError("Automatic selection requires a single-part video under 3 minutes and over 100 danmaku/minute")
    elif info["duration"] > 300 and confirmed_duration != info["duration"]:
        return False
    return True


def enforce_download_policy(info, *, metadata_duration, automatic=False, confirmed_duration=None):
    """Recheck stream limits without equating container timestamps to metadata.

    The caller has already validated metadata for this video's selected part.
    Allow two seconds of container rounding, but reconfirm larger increases.
    """
    seconds = info["duration"]
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS:
        raise ValueError("Downloaded video duration exceeds the supported limit")
    consent = (metadata_duration > 300 and confirmed_duration == metadata_duration
               and seconds <= metadata_duration + 2)
    return enforce_policy(info, automatic=automatic,
                          confirmed_duration=seconds if consent else None)


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
            if not isinstance(row, dict):
                continue
            bvid = row.get("bvid")
            if not isinstance(bvid, str) or not bvid or bvid in seen:
                continue
            seen.add(bvid)
            seconds = row.get("duration")
            if isinstance(seconds, str) and ":" in seconds:
                try:
                    seconds = sum(float(value) * 60 ** index for index, value in enumerate(reversed(seconds.split(":"))))
                except ValueError:
                    continue
            stat = row.get('stat')
            stat = stat if isinstance(stat, dict) else {}
            count = row.get("video_review", stat.get("danmaku"))
            if not eligible(seconds, count):
                continue
            try:
                info = await inspect_video(bvid)
                enforce_policy(info, automatic=True)
            except Exception:
                # An unavailable candidate must not hide later valid results.
                # CancelledError inherits BaseException and still propagates.
                continue
            return {"video": info, "topic": topic}
    return {"video": None, "topic": topic}
