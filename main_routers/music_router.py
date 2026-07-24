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

"""
Music Router

Handles music search / playback / lyric proxy endpoints.

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

import asyncio

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse, Response, JSONResponse, StreamingResponse
from cachetools import TTLCache
import httpx
from utils.music_crawlers import fetch_music_content, MUSIC_SOURCE_DOMAINS
from utils.cookies_login import load_cookies_from_file
from utils.logger_config import get_module_logger
from urllib.parse import unquote, urlparse, urljoin

router = APIRouter()

logger = get_module_logger(__name__, "Music")

# ---------------------------------------------------------------------------
#  pyncm_async compat patch — 1.8.2 uses Python 3.12+ f-string syntax in
#  cloud.py which triggers SyntaxError on older interpreters.  We patch the
#  source file on disk before import so the rest of the code works normally.
# ---------------------------------------------------------------------------
def _patch_pyncm_async() -> None:
    import os, sys, tempfile
    if sys.version_info >= (3, 12):
        return
    for p in sys.path:
        target = os.path.join(p, "pyncm_async", "apis", "cloud.py")
        if not os.path.isfile(target):
            continue
        try:
            with open(target, "r", encoding="utf-8") as f:
                code = f.read()
            if 'objectKey.replace("/", "%2F")' in code:
                if not os.access(target, os.W_OK):
                    logger.error("[Music] pyncm_async cloud.py is read-only, cannot patch: %s", target)
                    return
                code = code.replace(
                    'objectKey.replace("/", "%2F")',
                    "objectKey.replace('/', '%2F')",
                )
                fd, tmp_path = tempfile.mkstemp(
                    dir=os.path.dirname(target), prefix="cloud.py.", suffix=".tmp",
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(code)
                    os.replace(tmp_path, target)
                except Exception:
                    try:
                        os.remove(tmp_path)
                    except OSError as cleanup_exc:
                        logger.debug("[Music] Failed to remove temp file %s: %s", tmp_path, cleanup_exc)
                    raise
                logger.info("[Music] Patched pyncm_async cloud.py for Python <3.12 compat")
            return
        except Exception as exc:
            logger.error("[Music] Failed to patch pyncm_async: %s", exc)
            return

# pyncm_async 仅用于网易云 VIP 直链播放，import 偏重（~0.15s）且不在启动/greeting
# 链上，改成首次播放时再 import（含 <3.12 兼容补丁），由 module_warmup 预热。
pyncm_async = None  # type: ignore[assignment]
GetTrackAudio = None  # type: ignore[assignment,misc]
_PYNCM_AVAILABLE: bool | None = None  # None = 尚未尝试导入


def _ensure_pyncm() -> bool:
    """On first call, apply the patch and import pyncm_async, caching the result. Returns availability."""
    global pyncm_async, GetTrackAudio, _PYNCM_AVAILABLE
    # 显式强制不可用优先 → 降级。
    if _PYNCM_AVAILABLE is False:
        return False
    # 对象已就位（真 import 过 / 测试注入了 mock）→ 直接信任，不重导入。
    if pyncm_async is not None and GetTrackAudio is not None:
        _PYNCM_AVAILABLE = True
        return True
    _patch_pyncm_async()
    try:
        import pyncm_async as _pyncm
        from pyncm_async.apis.track import GetTrackAudio as _GetTrackAudio
        # 只补缺失的，保住测试可能注入的 mock。
        if pyncm_async is None:
            pyncm_async = _pyncm
        if GetTrackAudio is None:
            GetTrackAudio = _GetTrackAudio
        _PYNCM_AVAILABLE = True
    except Exception as _pyncm_err:
        pyncm_async = None  # type: ignore[assignment]
        GetTrackAudio = None  # type: ignore[assignment,misc]
        _PYNCM_AVAILABLE = False
        logger.error("[Music] pyncm_async unavailable, netease VIP playback disabled: %s", _pyncm_err)
    return _PYNCM_AVAILABLE


def _sync_pyncm_session_cookies(session, cookies: dict[str, str]) -> bool:
    """Sync NetEase cookies into the current pyncm_async session across API versions."""
    cookie_setters = []
    seen_cookie_jars = set()
    for cookie_jar in (
        getattr(session, "cookies", None),
        getattr(getattr(session, "client", None), "cookies", None),
    ):
        if cookie_jar is None or id(cookie_jar) in seen_cookie_jars:
            continue
        cookie_setter = getattr(cookie_jar, "set", None)
        if callable(cookie_setter):
            cookie_setters.append(cookie_setter)
            seen_cookie_jars.add(id(cookie_jar))

    if not cookie_setters:
        logger.warning("[音乐播放] pyncm_async Session 不支持 Cookie 注入，已跳过登录态同步")
        return False

    synced = False
    for cookie_setter in cookie_setters:
        try:
            for key, value in cookies.items():
                cookie_setter(key, value)
        except Exception as exc:
            logger.warning("[音乐播放] pyncm_async Cookie 注入失败，尝试下一个 CookieJar: %s", exc)
            continue
        synced = True

    if not synced:
        logger.warning("[音乐播放] pyncm_async Session 不支持 Cookie 注入，已跳过登录态同步")
    return synced

# ==================== 音乐代理缓存 ====================
# 仅缓存小文件（<10MB），大文件流式传输
MUSIC_PROXY_CACHE = TTLCache(
    maxsize=100 * 1024 * 1024,  # 100MB 内存预算（减小）
    ttl=3600 * 6,
    getsizeof=lambda item: len(item.get('body', b''))
)

STREAMING_SIZE_THRESHOLD = 10 * 1024 * 1024  # 10MB 以上流式传输
MAX_MUSIC_SIZE = 50 * 1024 * 1024
PLAYABLE_BINARY_CONTENT_TYPES = {'application/octet-stream', 'binary/octet-stream'}


def _is_playable_audio_content_type(content_type: str) -> bool:
    normalized = (content_type or '').split(';', 1)[0].strip().lower()
    return (
        normalized.startswith('audio/')
        or normalized.startswith('video/')
        or normalized in PLAYABLE_BINARY_CONTENT_TYPES
    )


def _is_allowed_music_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').lower()
    return parsed.scheme in ('http', 'https') and any(
        hostname == domain or hostname.endswith('.' + domain)
        for domain in MUSIC_SOURCE_DOMAINS
    )


async def _probe_audio_url(url: str) -> bool:
    """Verify that a fallback URL resolves to an allowed, playable audio response."""
    if not _is_allowed_music_url(url):
        return False
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36',
        'Referer': 'https://music.163.com/',
        'Accept': 'audio/*,*/*;q=0.8',
        'Accept-Encoding': 'identity',
        'Range': 'bytes=0-0',
    }
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
            current_url = url
            for _ in range(10):
                if not _is_allowed_music_url(current_url):
                    return False
                req = client.build_request('GET', current_url, headers=headers)
                response = await client.send(req, stream=True)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('location')
                    await response.aclose()
                    if not location:
                        return False
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code not in (200, 206):
                    await response.aclose()
                    return False
                content_type = response.headers.get('content-type', '')
                playable = _is_playable_audio_content_type(content_type)
                await response.aclose()
                return playable
    except (httpx.HTTPError, ValueError):
        return False
    return False

@router.get("/api/music/proxy")
async def proxy_music(url: str, request: Request):
    """
    Generic music proxy that works around CORS and Referer restrictions.
    Cache misses are streamed immediately. Complete non-range responses up to
    10MB are cached while they are forwarded.
    """
    range_header = request.headers.get('range')
    cache_key = url
    if not range_header and cache_key in MUSIC_PROXY_CACHE:
        cached = MUSIC_PROXY_CACHE[cache_key]
        cached_type = cached.get('content_type', 'audio/mpeg')
        logger.debug(f"[Music Proxy] 命中缓存: {url[:60]}..., 大小: {len(cached['body'])} bytes")
        return Response(
            content=cached['body'],
            media_type=cached_type,
            headers={
                'Content-Length': str(len(cached['body'])),
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'public, max-age=21600',
                'X-Cache': 'HIT'
            }
        )

    client = None
    resp = None
    try:
        if not url:
            return JSONResponse(content={"success": False, "error": "缺少URL参数"}, status_code=400)

        decoded_url = unquote(url)
        if not decoded_url.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "无效的URL"}, status_code=400)

        parsed = urlparse(decoded_url)
        hostname = (parsed.hostname or '').lower()

        if not any(hostname == domain or hostname.endswith('.' + domain) for domain in MUSIC_SOURCE_DOMAINS):
            logger.warning(f"[Music Proxy] 非法域名请求: {hostname}")
            return JSONResponse(content={"success": False, "error": f"不允许代理该域名: {hostname}"}, status_code=403)

        request_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Referer': f'{parsed.scheme}://{hostname}/',
            'Accept': '*/*',
            'Accept-Encoding': 'identity',
        }
        if range_header:
            request_headers['Range'] = range_header
        if_range = request.headers.get('if-range')
        if if_range:
            request_headers['If-Range'] = if_range

        client = httpx.AsyncClient(timeout=60.0, follow_redirects=False)
        current_url = decoded_url
        for _ in range(10):
            if not _is_allowed_music_url(current_url):
                hostname = (urlparse(current_url).hostname or '').lower()
                logger.warning(f"[Music Proxy] 重定向目标域名不在白名单: {hostname}")
                await client.aclose()
                client = None
                return JSONResponse(content={"success": False, "error": "重定向目标域名不在白名单"}, status_code=403)
            req = client.build_request("GET", current_url, headers=request_headers)
            resp = await client.send(req, stream=True)
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            location = resp.headers.get('location')
            await resp.aclose()
            resp = None
            if not location:
                await client.aclose()
                client = None
                return JSONResponse(content={"success": False, "error": "重定向响应缺少 Location 头"}, status_code=502)
            current_url = urljoin(current_url, location)
        else:
            await client.aclose()
            client = None
            return JSONResponse(content={"success": False, "error": "重定向次数过多"}, status_code=502)

        resp.raise_for_status()
        content_type = resp.headers.get('Content-Type', 'audio/mpeg').split(';', 1)[0].strip()
        if not _is_playable_audio_content_type(content_type):
            logger.warning(f"[Music Proxy] 非音频内容类型: {content_type}")
            await resp.aclose()
            await client.aclose()
            resp = client = None
            return JSONResponse(content={"success": False, "error": "音乐源返回了无效内容（非音频格式）"}, status_code=502)

        content_length = resp.headers.get('Content-Length')
        declared_size = None
        if content_length:
            try:
                declared_size = int(content_length)
            except (ValueError, TypeError):
                content_length = None
        content_range = resp.headers.get('Content-Range', '')
        total_size = None
        if content_range and '/' in content_range:
            try:
                total_size = int(content_range.rsplit('/', 1)[1])
            except (TypeError, ValueError):
                pass
        if (declared_size is not None and declared_size > MAX_MUSIC_SIZE) or (
            total_size is not None and total_size > MAX_MUSIC_SIZE
        ):
            logger.warning(f"[Music Proxy] 音乐文件过大: {total_size or declared_size}")
            await resp.aclose()
            await client.aclose()
            resp = client = None
            return JSONResponse(content={"success": False, "error": "音乐文件超过大小限制 (50MB)"}, status_code=413)

        response_headers = {
            'Cache-Control': 'no-cache',
            'X-Cache': 'RANGE' if range_header else 'MISS',
            'Accept-Ranges': resp.headers.get('Accept-Ranges', 'bytes'),
        }
        if content_range:
            response_headers['Content-Range'] = content_range
        if content_length:
            response_headers['Content-Length'] = content_length

        response = StreamingResponse(
            _stream_music_response(
                client,
                resp,
                cache_key=cache_key if not range_header and resp.status_code == 200 else None,
                content_type=content_type,
            ),
            media_type=content_type,
            headers=response_headers,
            status_code=resp.status_code,
        )
        # The generator now owns both resources and closes them on EOF, error,
        # or client disconnect.
        client = resp = None
        return response

    except httpx.HTTPStatusError as e:
        if resp is not None:
            await resp.aclose()
        if client is not None:
            await client.aclose()
        logger.error(f"[Music Proxy] HTTP错误: {e.response.status_code}")
        return JSONResponse(content={"success": False, "error": f"请求失败: {e.response.status_code}"}, status_code=e.response.status_code)
    except httpx.TimeoutException:
        if resp is not None:
            await resp.aclose()
        if client is not None:
            await client.aclose()
        return JSONResponse(content={"success": False, "error": "请求超时"}, status_code=504)
    except Exception as e:
        if resp is not None:
            await resp.aclose()
        if client is not None:
            await client.aclose()
        logger.error(f"[Music Proxy] 代理失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": "请求处理失败"}, status_code=500)


async def _stream_music_response(client, response, cache_key, content_type):
    """Forward one open upstream response and tee complete small bodies to cache."""
    total = 0
    cache_body = bytearray() if cache_key else None
    completed = False
    try:
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_MUSIC_SIZE:
                logger.warning(f"[Music Proxy] 未知长度音乐流超过大小限制: {total}")
                return
            if cache_body is not None:
                if total <= STREAMING_SIZE_THRESHOLD:
                    cache_body.extend(chunk)
                else:
                    cache_body = None
            yield chunk
        completed = True
        if cache_key and cache_body is not None:
            MUSIC_PROXY_CACHE[cache_key] = {
                'body': bytes(cache_body),
                'content_type': content_type,
            }
    except Exception as exc:
        logger.error(f"[Music Proxy] 流式传输错误: {exc}")
    finally:
        if not completed and cache_key:
            logger.debug(f"[Music Proxy] 流未完整结束，不写缓存: {cache_key[:60]}...")
        await response.aclose()
        await client.aclose()


@router.get("/api/music/domains")
async def get_music_domains():
    """
    Return the domain list of all music sources for the frontend to register in its dynamic whitelist.
    Unifies the whitelist pool and crawler pool, automatically adding the crawler pool to the whitelist.
    """
    return {
        "success": True,
        "domains": list(MUSIC_SOURCE_DOMAINS)
    }

@router.get("/api/music/search")
async def search_music(
    query: str = Query(default="", max_length=200),
    limit: int = Query(default=10, ge=1, le=50)
):
    """
    Smart music dispatch route; uniformly calls fetch_music_content from music_crawlers.
    """
    query = query.strip()
    
    logger.info(f"[音乐API] 收到搜索请求: '{query}'")
    
    # 空白输入校验
    if not query:
        logger.warning("[音乐API] 搜索关键词为空,返回失败结果")
        return {
            "success": False,  # 【核心修复】标记为失败
            "data": [],
            "error": "EMPTY_QUERY",  # 填入 error 字段方便前端捕获
            "message": "搜索关键词不能为空"
        }
    
    # 异常保护
    try:
        # 确保至少返回 5 个候选项供前端智能匹配，同时尊重用户传入的 limit
        effective_limit = max(limit, 5)
        results = await fetch_music_content(keyword=query, limit=effective_limit)
        
        if results.get('success'):
            track_count = len(results.get('data', []))
            logger.info(f"[音乐API] 搜索成功，返回 {track_count} 首音乐")
        else:
            error = results.get('error', '未知错误')
            logger.warning(f"[音乐API] 搜索失败: {error}")
            # 统一失败返回结构
            return {
                "success": False,
                "data": [],
                "error": error,
                "message": results.get("message") or error or "音乐搜索失败"
            }
        
        return results
        
    except Exception as e:
        logger.error(f"[音乐API] 搜索异常: {type(e).__name__}: {e}")
        return {
            "success": False,
            "data": [],
            "error": "MUSIC_SEARCH_ERROR",
            "message": "音乐搜索服务异常，请稍后重试"
        }

@router.get("/api/music/play/netease/{song_id}")
async def play_netease_music(song_id: str):
    """
    NetEase Cloud Music VIP smart-redirect route:
    uses the backend MUSIC_U cookie to obtain the real high-quality / authenticated direct link, then 307-redirects the frontend to play it.
    """
    if not (song_id.isascii() and song_id.isdecimal()):
        return JSONResponse(content={"success": False, "error": "invalid song_id"}, status_code=400)
    song_id_int = int(song_id)

    if not _ensure_pyncm():
        fallback_url = f"https://music.163.com/song/media/outer/url?id={song_id_int}.mp3"
        if await _probe_audio_url(fallback_url):
            logger.warning("[音乐播放] pyncm_async 不可用，使用已验证的公开外链")
            return RedirectResponse(url=fallback_url)
        return JSONResponse(content={"success": False, "error": "歌曲没有可用音源"}, status_code=502)

    try:
        # 加载 Cookie 并同步到 pyncm_async 会话
        cookies = await asyncio.to_thread(load_cookies_from_file, 'netease')
        if cookies:
            session = pyncm_async.GetCurrentSession()
            _sync_pyncm_session_cookies(session, cookies)

        # 获取真实播放地址 (IDs 接受列表)
        # 默认获取 standard 标准音质，VIP 账户通常可获得更多 Token 授权
        res = await GetTrackAudio([song_id_int])

        if res and res.get('data') and len(res['data']) > 0:
            track_info = res['data'][0]
            real_url = track_info.get('url')

            if real_url:
                logger.info(f"[音乐播放] 成功解析歌曲 {song_id_int} 的 VIP/鉴权直链")
                return RedirectResponse(url=real_url)

    except Exception as e:
        logger.error(f"[音乐播放] 解析歌曲 {song_id_int} 真实地址时发生异常: {e}")

    # Fallback: 如果解析失败或无 Cookie，降级使用免登录的 outer/url 外链
    fallback_url = f"https://music.163.com/song/media/outer/url?id={song_id_int}.mp3"
    if await _probe_audio_url(fallback_url):
        logger.warning(f"[音乐播放] 无法获取歌曲 {song_id_int} 的真实链接，使用已验证的公开外链")
        return RedirectResponse(url=fallback_url)
    logger.warning(f"[音乐播放] 歌曲 {song_id_int} 没有可用音源")
    return JSONResponse(content={"success": False, "error": "歌曲没有可用音源"}, status_code=502)
