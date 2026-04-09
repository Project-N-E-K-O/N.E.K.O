# 音乐路由

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, RedirectResponse
from utils.music_crawlers import fetch_music_content
from utils.cookies_login import load_cookies_from_file
from utils.logger_config import get_module_logger

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