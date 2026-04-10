# 音乐路由

from fastapi import APIRouter, Query
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

_patch_pyncm_async()

try:
    import pyncm_async
    from pyncm_async.apis.track import GetTrackAudio
    _PYNCM_AVAILABLE = True
except Exception as _pyncm_err:
    pyncm_async = None  # type: ignore[assignment]
    GetTrackAudio = None  # type: ignore[assignment,misc]
    _PYNCM_AVAILABLE = False
    logger.error("[Music] pyncm_async unavailable, netease VIP playback disabled: %s", _pyncm_err)

# ==================== 音乐代理缓存 ====================
# 仅缓存小文件（<10MB），大文件流式传输
MUSIC_PROXY_CACHE = TTLCache(
    maxsize=100 * 1024 * 1024,  # 100MB 内存预算（减小）
    ttl=3600 * 6,
    getsizeof=lambda item: len(item.get('body', b''))
)

STREAMING_SIZE_THRESHOLD = 10 * 1024 * 1024  # 10MB 以上流式传输

@router.get("/api/music/proxy")
async def proxy_music(url: str):
    """
    通用音乐代理，解决跨域和 Referer 限制问题。
    - <10MB: 完整缓存，快速返回
    - ≥10MB: 流式传输，边下边播
    """
    cache_key = url
    if cache_key in MUSIC_PROXY_CACHE:
        cached = MUSIC_PROXY_CACHE[cache_key]
        cached_type = cached.get('content_type', 'audio/mpeg')
        logger.debug(f"[Music Proxy] 命中缓存: {url[:60]}..., 大小: {len(cached['body'])} bytes")
        return Response(
            content=cached['body'],
            media_type=cached_type,
            headers={
                'Content-Length': str(len(cached['body'])),
                'Cache-Control': 'public, max-age=21600',
                'X-Cache': 'HIT'
            }
        )

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
            'Referer': 'https://music.163.com/',
            'Accept': '*/*',
            'Accept-Encoding': 'identity',
        }

        MAX_MUSIC_SIZE = 50 * 1024 * 1024  # 50MB 上限

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            # 用 stream=True 探测，只读 headers 不拉 body，大文件不会被白白下载一遍
            current_url = decoded_url
            resp = None
            for _ in range(10):
                parsed = urlparse(current_url)
                hostname = (parsed.hostname or '').lower()
                if not any(hostname == domain or hostname.endswith('.' + domain) for domain in MUSIC_SOURCE_DOMAINS):
                    logger.warning(f"[Music Proxy] 重定向目标域名不在白名单: {hostname}")
                    return JSONResponse(content={"success": False, "error": "重定向目标域名不在白名单"}, status_code=403)
                req = client.build_request("GET", current_url, headers=request_headers)
                resp = await client.send(req, stream=True)
                if resp.status_code in (301, 302, 303, 307, 308):
                    await resp.aclose()
                    location = resp.headers.get('location')
                    if not location:
                        return JSONResponse(content={"success": False, "error": "重定向响应缺少 Location 头"}, status_code=502)
                    current_url = urljoin(current_url, location)
                    continue
                break
            else:
                # 重定向次数用尽仍未拿到最终响应
                if resp is not None:
                    await resp.aclose()
                return JSONResponse(content={"success": False, "error": "重定向次数过多"}, status_code=502)
            resp.raise_for_status()

            content_type = resp.headers.get('Content-Type', 'audio/mpeg').split(';', 1)[0].strip()
            if 'audio' not in content_type and 'video' not in content_type:
                await resp.aclose()
                logger.warning(f"[Music Proxy] 非音频内容类型: {content_type}")
                return JSONResponse(content={"success": False, "error": "音乐源返回了无效内容（非音频格式）"}, status_code=502)

            content_length = resp.headers.get('Content-Length')
            declared_size = 0
            if content_length:
                try:
                    declared_size = int(content_length)
                    if declared_size > MAX_MUSIC_SIZE:
                        await resp.aclose()
                        logger.warning(f"[Music Proxy] 音乐文件过大: {declared_size}")
                        return JSONResponse(content={"success": False, "error": "音乐文件超过大小限制 (50MB)"}, status_code=413)
                except (ValueError, TypeError):
                    pass

            if declared_size >= STREAMING_SIZE_THRESHOLD:
                # 大文件：关掉探测流，交给 _stream_music 用独立 client 流式传输
                # 传 current_url（已解析完重定向的最终地址）避免重复跟重定向
                await resp.aclose()
                logger.debug(f"[Music Proxy] 流式传输: {url[:60]}..., 预大小: {declared_size}")
                headers = {
                    'Cache-Control': 'no-cache',
                    'X-Cache': 'STREAM'
                }
                return StreamingResponse(
                    _stream_music(current_url, request_headers, MAX_MUSIC_SIZE),
                    media_type=content_type,
                    headers=headers
                )

            # 小文件：直接从已打开的探测流中读取 body
            try:
                body = bytearray()
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    body.extend(chunk)
                    if len(body) > MAX_MUSIC_SIZE:
                        logger.warning(f"[Music Proxy] 音乐文件过大 (实际读取): {len(body)}")
                        return JSONResponse(content={"success": False, "error": "音乐文件超过大小限制 (50MB)"}, status_code=413)
            finally:
                await resp.aclose()

            body_bytes = bytes(body)

            if len(body_bytes) < STREAMING_SIZE_THRESHOLD:
                MUSIC_PROXY_CACHE[cache_key] = {'body': body_bytes, 'content_type': content_type}

            logger.debug(f"[Music Proxy] 小文件返回: {url[:60]}..., 大小: {len(body_bytes)}")
            return Response(
                content=body_bytes,
                media_type=content_type,
                headers={
                    'Content-Length': str(len(body_bytes)),
                    'Cache-Control': 'public, max-age=21600',
                    'X-Cache': 'MISS'
                }
            )

    except httpx.HTTPStatusError as e:
        logger.error(f"[Music Proxy] HTTP错误: {e.response.status_code}")
        return JSONResponse(content={"success": False, "error": f"请求失败: {e.response.status_code}"}, status_code=e.response.status_code)
    except httpx.TimeoutException:
        return JSONResponse(content={"success": False, "error": "请求超时"}, status_code=504)
    except Exception as e:
        logger.error(f"[Music Proxy] 代理失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": "请求处理失败"}, status_code=500)


async def _stream_music(url, headers, max_size):
    """流式生成器：内部创建独立 client，自己处理重定向和流式读取"""
    client = httpx.AsyncClient(timeout=60.0, follow_redirects=False)
    try:
        current_url = url
        for _ in range(10):
            parsed = urlparse(current_url)
            hostname = (parsed.hostname or '').lower()
            if not any(hostname == domain or hostname.endswith('.' + domain) for domain in MUSIC_SOURCE_DOMAINS):
                logger.warning(f"[Music Proxy] 流式重定向目标域名不在白名单: {hostname}")
                break
            try:
                req = client.build_request("GET", current_url, headers=headers)
                resp = await client.send(req, stream=True)
                if resp.status_code in (301, 302, 303, 307, 308):
                    await resp.aclose()
                    location = resp.headers.get('location')
                    if not location:
                        break
                    current_url = urljoin(current_url, location)
                    continue
                resp.raise_for_status()
                try:
                    total = 0
                    async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                        total += len(chunk)
                        if total > max_size:
                            break
                        yield chunk
                finally:
                    await resp.aclose()
                break
            except Exception as e:
                logger.error(f"[Music Proxy] 流式传输错误: {e}")
                break
    finally:
        await client.aclose()

@router.get("/api/music/domains")
async def get_music_domains():
    """
    获取所有音乐源的域名列表，供前端白名单动态注册使用。
    统一白名单池和爬虫池，自动把爬虫池加到白名单
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
    智能音乐分发路由，统一调用 music_crawlers 中的 fetch_music_content。
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
    网易云 VIP 音乐智能跳转路由：
    利用后端 MUSIC_U Cookie 获取真实高音质/鉴权直链，通过 307 重定向至前端播放。
    """
    if not (song_id.isascii() and song_id.isdecimal()):
        return JSONResponse(content={"success": False, "error": "invalid song_id"}, status_code=400)
    song_id_int = int(song_id)

    if not _PYNCM_AVAILABLE:
        fallback_url = f"https://music.163.com/song/media/outer/url?id={song_id_int}.mp3"
        logger.warning("[音乐播放] pyncm_async 不可用，直接使用公开外链")
        return RedirectResponse(url=fallback_url)

    try:
        # 加载 Cookie 并同步到 pyncm_async 会话
        cookies = load_cookies_from_file('netease')
        if cookies:
            session = pyncm_async.GetCurrentSession()
            # 兼容性处理：pyncm_async 内部使用 httpx，直接注入 cookiejar
            for k, v in cookies.items():
                session.client.cookies.set(k, v)

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
    logger.warning(f"[音乐播放] 无法获取歌曲 {song_id_int} 的真实链接，降级使用公开外链")
    return RedirectResponse(url=fallback_url)