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
``.agent/rules/neko-guide.md`` (§\"API URLs have no trailing slash\") for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

import asyncio

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse, Response, JSONResponse, StreamingResponse
from cachetools import TTLCache
import httpx
from utils.music_crawlers import (
    fetch_music_content,
    MUSIC_SOURCE_DOMAINS,
    sync_pyncm_session_cookies as _sync_pyncm_session_cookies,
)
from utils.cookies_login import load_cookies_from_file
from utils.logger_config import get_module_logger
from utils.music_audio import is_allowed_audio_url, is_audio_content_type, is_netease_candidate_url, mark_audio_unavailable, probe_audio_url, send_audio_request
from urllib.parse import urlparse, urljoin, quote

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

# pyncm_async is used only for NetEase VIP direct-link playback; its import is heavy (~0.15s) and outside startup/greeting
# paths, so import on first playback (including the <3.12 compatibility patch), warmed by module_warmup.
pyncm_async = None  # type: ignore[assignment]
GetTrackAudio = None  # type: ignore[assignment,misc]
_PYNCM_AVAILABLE: bool | None = None  # None = import not yet attempted


def _ensure_pyncm() -> bool:
    """On first call, apply the patch and import pyncm_async, caching the result. Returns availability."""
    global pyncm_async, GetTrackAudio, _PYNCM_AVAILABLE
    # Explicitly forced unavailability takes precedence → downgrade.
    if _PYNCM_AVAILABLE is False:
        return False
    # An existing object (real import or test-injected mock) is trusted directly without reimporting.
    if pyncm_async is not None and GetTrackAudio is not None:
        _PYNCM_AVAILABLE = True
        return True
    _patch_pyncm_async()
    try:
        import pyncm_async as _pyncm
        from pyncm_async.apis.track import GetTrackAudio as _GetTrackAudio
        # Fill only missing values to preserve mocks injected by tests.
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


# ==================== Music-proxy cache ====================
# Cache only small files (<10MB); stream larger files
MUSIC_PROXY_CACHE = TTLCache(
    maxsize=100 * 1024 * 1024,  # 100MB memory budget (reduced)
    ttl=3600 * 6,
    getsizeof=lambda item: len(item.get('body', b''))
)

STREAMING_SIZE_THRESHOLD = 10 * 1024 * 1024  # Stream files above 10MB
MAX_MUSIC_SIZE = 50 * 1024 * 1024
PLAYABLE_BINARY_CONTENT_TYPES = {'application/octet-stream', 'binary/octet-stream'}


class _MusicStreamLimitExceeded(RuntimeError):
    """Abort a committed stream when the upstream body exceeds our hard limit."""


def _is_playable_audio_content_type(content_type: str) -> bool:
    return is_audio_content_type(content_type)


def _is_allowed_music_url(url: str) -> bool:
    return is_allowed_audio_url(url)


async def _probe_audio_url(url: str) -> bool | None:
    """Verify that a fallback URL resolves to an allowed, playable audio response."""
    return await probe_audio_url(url, referer='https://music.163.com/')

@router.get("/api/music/proxy")
async def proxy_music(url: str, request: Request, netease_id: str = ''):
    """
    Generic music proxy that works around CORS and Referer restrictions.
    Cache misses are streamed immediately. Complete non-range responses up to
    10MB are cached while they are forwarded.
    """
    range_header = request.headers.get('range')
    candidate_url = '/api/music/play/netease/' + netease_id
    if not is_netease_candidate_url(candidate_url):
        candidate_url = None
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

        # FastAPI already decoded the query parameter; preserve signed URL escapes.
        upstream_url = url
        if urlparse(upstream_url).scheme != 'https':
            return JSONResponse(content={"success": False, "error": "无效的URL"}, status_code=400)

        parsed = urlparse(upstream_url)
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

        client = await asyncio.to_thread(httpx.AsyncClient, timeout=httpx.Timeout(60.0, connect=5.0), follow_redirects=False)
        current_url = upstream_url
        for _ in range(10):
            if not _is_allowed_music_url(current_url):
                mark_audio_unavailable(url, candidate_url=candidate_url)
                hostname = (urlparse(current_url).hostname or '').lower()
                logger.warning(f"[Music Proxy] 重定向目标域名不在白名单: {hostname}")
                await client.aclose()
                client = None
                return JSONResponse(content={"success": False, "error": "重定向目标域名不在白名单"}, status_code=403)
            req = client.build_request("GET", current_url, headers=request_headers)
            resp = await send_audio_request(client, req)
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            location = resp.headers.get('location')
            await resp.aclose()
            resp = None
            if not location:
                mark_audio_unavailable(url, candidate_url=candidate_url)
                await client.aclose()
                client = None
                return JSONResponse(content={"success": False, "error": "重定向响应缺少 Location 头"}, status_code=502)
            current_url = urljoin(current_url, location)
        else:
            mark_audio_unavailable(url, candidate_url=candidate_url)
            await client.aclose()
            client = None
            return JSONResponse(content={"success": False, "error": "重定向次数过多"}, status_code=502)

        resp.raise_for_status()
        content_type = resp.headers.get('Content-Type', 'audio/mpeg').split(';', 1)[0].strip()
        if not _is_playable_audio_content_type(content_type):
            mark_audio_unavailable(url, candidate_url=candidate_url)
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
            mark_audio_unavailable(url, candidate_url=candidate_url)
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
        # Do not forward the upstream Content-Length. The upstream can understate
        # it, while our generator still stops at MAX_MUSIC_SIZE; advertising that
        # untrusted length would leave a truncated response with invalid framing.

        response = StreamingResponse(
            _stream_music_response(
                client,
                resp,
                cache_key=cache_key if not range_header and resp.status_code == 200 else None,
                content_type=content_type,
                source_url=url,
                candidate_url=candidate_url,
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
        mark_audio_unavailable(url, candidate_url=candidate_url)
        if resp is not None:
            await resp.aclose()
        if client is not None:
            await client.aclose()
        logger.error(f"[Music Proxy] HTTP错误: {e.response.status_code}")
        return JSONResponse(content={"success": False, "error": f"请求失败: {e.response.status_code}"}, status_code=e.response.status_code)
    except httpx.TimeoutException:
        mark_audio_unavailable(url, connection_failure=True, candidate_url=candidate_url)
        if resp is not None:
            await resp.aclose()
        if client is not None:
            await client.aclose()
        return JSONResponse(content={"success": False, "error": "请求超时"}, status_code=504)
    except httpx.RequestError as e:
        mark_audio_unavailable(url, connection_failure=True, candidate_url=candidate_url)
        if resp is not None:
            await resp.aclose()
        if client is not None:
            await client.aclose()
        logger.warning('[Music Proxy] 音源连接失败 (%s)', type(e).__name__, exc_info=True)
        return JSONResponse(content={"success": False, "error": "音源连接失败"}, status_code=502)
    except Exception as e:
        if resp is not None:
            await resp.aclose()
        if client is not None:
            await client.aclose()
        logger.error('[Music Proxy] 代理失败 (%s)', type(e).__name__, exc_info=True)
        return JSONResponse(content={"success": False, "error": "请求处理失败"}, status_code=500)


async def _stream_music_response(client, response, cache_key, content_type, source_url=None, candidate_url=None):
    """Forward one open upstream response and tee complete small bodies to cache."""
    total = 0
    cache_body = bytearray() if cache_key else None
    completed = False
    try:
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_MUSIC_SIZE:
                logger.warning(f"[Music Proxy] 未知长度音乐流超过大小限制: {total}")
                raise _MusicStreamLimitExceeded(total)
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
    # Headers are committed here, so re-raising is how clients learn the body is incomplete.
    except _MusicStreamLimitExceeded:
        if source_url:
            mark_audio_unavailable(source_url, candidate_url=candidate_url)
        raise
    except Exception as exc:
        if source_url:
            mark_audio_unavailable(source_url, connection_failure=isinstance(exc, httpx.RequestError), candidate_url=candidate_url)
        logger.error(f"[Music Proxy] 流式传输错误: {exc}")
        raise
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
    
    # Validate blank input
    if not query:
        logger.warning("[音乐API] 搜索关键词为空,返回失败结果")
        return {
            "success": False,  # [Core fix] Mark as failed
            "data": [],
            "error": "EMPTY_QUERY",  # Fill the error field for frontend handling
            "message": "搜索关键词不能为空"
        }
    
    # Exception protection
    try:
        # Ensure at least 5 candidates for frontend intelligent matching while respecting the supplied limit
        effective_limit = max(limit, 5)
        results = await fetch_music_content(keyword=query, limit=effective_limit)
        
        if results.get('success'):
            track_count = len(results.get('data', []))
            logger.info(f"[音乐API] 搜索成功，返回 {track_count} 首音乐")
        else:
            error = results.get('error', '未知错误')
            logger.warning(f"[音乐API] 搜索失败: {error}")
            # Consistent failure response structure
            return {
                "success": False,
                "data": [],
                "error": error,
                "message": results.get("message") or error or "音乐搜索失败"
            }
        
        # Short-lived authenticated audio URLs stay inside recommendation checks.
        def public_track(track):
            return {key: value for key, value in track.items() if key != '_audio_url'}

        results = {**results, 'data': [public_track(track) for track in results.get('data', [])]}
        best_match = results.get('best_match')
        if best_match and isinstance(best_match.get('resource'), dict):
            results['best_match'] = {**best_match, 'resource': public_track(best_match['resource'])}
        return results
        
    except Exception as e:
        logger.error(f"[音乐API] 搜索异常: {type(e).__name__}: {e}")
        return {
            "success": False,
            "data": [],
            "error": "MUSIC_SEARCH_ERROR",
            "message": "音乐搜索服务异常，请稍后重试"
        }

def _netease_proxy_redirect(url, song_id):
    # Upgrade only the scheme, retaining signed path/query bytes unchanged.
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    if not is_allowed_audio_url(url):
        return None
    return RedirectResponse(url=f'/api/music/proxy?url={quote(url, safe="")}&netease_id={song_id}')


@router.get("/api/music/play/netease/{song_id}")
async def play_netease_music(song_id: str):
    """
    NetEase Cloud Music VIP smart-redirect route:
    resolves authorized audio, then redirects through the shared playback proxy
    so actual playback failures invalidate the stable recommendation candidate.
    """
    if not (0 < len(song_id) <= 20 and song_id.isascii() and song_id.isdecimal()):
        return JSONResponse(content={"success": False, "error": "invalid song_id"}, status_code=400)
    song_id_int = int(song_id)

    if not _ensure_pyncm():
        fallback_url = f"https://music.163.com/song/media/outer/url?id={song_id_int}.mp3"
        probed = await _probe_audio_url(fallback_url)
        if probed:
            logger.warning("[音乐播放] pyncm_async 不可用，使用已验证的公开外链")
            return _netease_proxy_redirect(fallback_url, song_id_int)
        if probed is None:
            return JSONResponse(content={"success": False, "error": "AUDIO_CHECK_BUSY"}, status_code=503)
        mark_audio_unavailable(fallback_url, candidate_url=f'/api/music/play/netease/{song_id_int}')
        return JSONResponse(content={"success": False, "error": "歌曲没有可用音源"}, status_code=502)

    try:
        # Load cookies and synchronize them to the pyncm_async session
        cookies = await asyncio.to_thread(load_cookies_from_file, 'netease')
        session = pyncm_async.GetCurrentSession()
        _sync_pyncm_session_cookies(session, cookies)

        # Get real playback URLs (IDs accepts a list)
        # Request standard audio quality by default; VIP accounts usually receive additional token authorization
        res = await GetTrackAudio([song_id_int])

        if res and res.get('data') and len(res['data']) > 0:
            track_info = res['data'][0]
            real_url = track_info.get('url')

            if real_url and not track_info.get('freeTrialInfo'):
                response = _netease_proxy_redirect(real_url, song_id_int)
                if response is None:
                    raise ValueError('Unsafe playback URL')
                logger.info(f"[音乐播放] 成功解析歌曲 {song_id_int} 的 VIP/鉴权直链")
                return response

    except Exception as e:
        logger.error(f"[音乐播放] 解析歌曲 {song_id_int} 真实地址时发生异常: {e}")

    # Fallback: if parsing fails or cookies are absent, use login-free outer/url external links
    fallback_url = f"https://music.163.com/song/media/outer/url?id={song_id_int}.mp3"
    probed = await _probe_audio_url(fallback_url)
    if probed:
        logger.warning(f"[音乐播放] 无法获取歌曲 {song_id_int} 的真实链接，使用已验证的公开外链")
        return _netease_proxy_redirect(fallback_url, song_id_int)
    if probed is None:
        return JSONResponse(content={"success": False, "error": "AUDIO_CHECK_BUSY"}, status_code=503)
    mark_audio_unavailable(fallback_url, candidate_url=f'/api/music/play/netease/{song_id_int}')
    logger.warning(f"[音乐播放] 歌曲 {song_id_int} 没有可用音源")
    return JSONResponse(content={"success": False, "error": "歌曲没有可用音源"}, status_code=502)
