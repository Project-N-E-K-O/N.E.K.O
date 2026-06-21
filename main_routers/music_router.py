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
import time
from collections import deque
from typing import Any

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

_MUSIC_PLAYER_BRIDGE_EVENT_TTL_SECONDS = 90.0
_MUSIC_PLAYER_BRIDGE_EVENT_LIMIT = 240
_MUSIC_PLAYER_SURFACE_TTL_SECONDS = 8.0
_MUSIC_PLAYER_PENDING_TTL_SECONDS = 12.0
_MUSIC_PLAYER_ACTIVE_TTL_SECONDS = 35.0
_MUSIC_PLAYER_STATE_TTL_SECONDS = 75.0

_music_player_bridge_lock = asyncio.Lock()
_music_player_bridge_seq = 0
_music_player_bridge_events: deque[dict[str, Any]] = deque(maxlen=_MUSIC_PLAYER_BRIDGE_EVENT_LIMIT)
_music_player_surfaces: dict[str, dict[str, Any]] = {}
_music_player_busy_until: dict[str, float] = {}
_music_player_owner: str | None = None
_music_player_playback_id: str | None = None
_music_player_latest_state: dict[str, Any] | None = None
_music_player_latest_state_expire_at = 0.0


def _bridge_string(value: Any, *, max_len: int = 256) -> str:
    if not isinstance(value, str):
        return ""
    return value[:max_len]


def _sanitize_bridge_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if abs(value) < 1_000_000_000_000 else 0
    if isinstance(value, str):
        return value[:2048]
    if isinstance(value, list):
        return [_sanitize_bridge_value(item, depth=depth + 1) for item in value[:24]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:64]:
            if not isinstance(key, str):
                continue
            result[key[:96]] = _sanitize_bridge_value(item, depth=depth + 1)
        return result
    return None


def _purge_music_player_bridge(now: float) -> None:
    global _music_player_owner, _music_player_playback_id, _music_player_latest_state, _music_player_latest_state_expire_at

    for sender, surface in list(_music_player_surfaces.items()):
        if float(surface.get("expire_at") or 0) <= now:
            _music_player_surfaces.pop(sender, None)

    for sender, expire_at in list(_music_player_busy_until.items()):
        if expire_at <= now:
            _music_player_busy_until.pop(sender, None)

    if _music_player_latest_state is not None and _music_player_latest_state_expire_at <= now:
        _music_player_latest_state = None
        _music_player_latest_state_expire_at = 0.0
        _music_player_owner = None
        _music_player_playback_id = None

    while _music_player_bridge_events and float(_music_player_bridge_events[0].get("expire_at") or 0) <= now:
        _music_player_bridge_events.popleft()


def _normalize_music_surface(sender: str, payload: dict[str, Any], now: float) -> dict[str, Any]:
    mode = _bridge_string(payload.get("mode") or payload.get("surface") or payload.get("kind"), max_len=32)
    if mode not in {"compact", "full", "pet", "web", "unknown", "minimized"}:
        mode = "unknown"
    active = bool(payload.get("active"))
    focused = bool(payload.get("focused", active))
    visible = bool(payload.get("visible", active or focused))
    return {
        "sender": sender,
        "mode": mode,
        "active": active,
        "focused": focused,
        "visible": visible,
        "reason": _bridge_string(payload.get("reason"), max_len=64),
        "updated_at": now,
        "expire_at": now + _MUSIC_PLAYER_SURFACE_TTL_SECONDS,
    }


def _choose_music_player_active_surface(now: float) -> dict[str, Any] | None:
    _purge_music_player_bridge(now)
    candidates = [
        surface for surface in _music_player_surfaces.values()
        if surface.get("active") or surface.get("focused") or surface.get("visible")
    ]
    if not candidates:
        return None

    mode_weight = {"full": 4, "compact": 3, "web": 2, "pet": 1, "unknown": 0, "minimized": -1}

    def sort_key(surface: dict[str, Any]) -> tuple[int, int, int, float]:
        active_weight = 2 if surface.get("active") else (1 if surface.get("focused") else 0)
        visible_weight = 1 if surface.get("visible") else 0
        return (
            active_weight,
            visible_weight,
            mode_weight.get(str(surface.get("mode") or "unknown"), 0),
            float(surface.get("updated_at") or 0),
        )

    selected = max(candidates, key=sort_key)
    return {
        key: value for key, value in selected.items()
        if key not in {"expire_at"}
    }


def _append_music_player_bridge_event(
    event_type: str,
    sender: str,
    payload: dict[str, Any],
    surface: dict[str, Any],
    now: float,
) -> int:
    global _music_player_bridge_seq
    _music_player_bridge_seq += 1
    _music_player_bridge_events.append({
        "seq": _music_player_bridge_seq,
        "type": event_type,
        "sender": sender,
        "payload": payload,
        "surface": surface,
        "ts": int(now * 1000),
        "expire_at": now + _MUSIC_PLAYER_BRIDGE_EVENT_TTL_SECONDS,
    })
    return _music_player_bridge_seq


def _public_music_player_bridge_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in event.items()
        if key != "expire_at"
    }


def _music_player_playback_matches(playback_id: str) -> bool:
    return not _music_player_playback_id or bool(playback_id and playback_id == _music_player_playback_id)


@router.post("/api/music/player/bridge")
async def post_music_player_bridge(request: Request):
    """
    Short-lived cross-window music player bridge.

    Electron compact chat and full chat can run in isolated sessions, so browser
    BroadcastChannel/localStorage are not enough to keep one visible player and
    one audio owner. This endpoint stores only TTL-bound coordination events.
    """
    try:
        raw_body = await request.json()
    except Exception:
        raw_body = {}
    if not isinstance(raw_body, dict):
        raw_body = {}

    from .system_router import _validate_local_mutation_request

    validation_error = _validate_local_mutation_request(
        request,
        payload=raw_body,
        error_defaults={"success": False},
    )
    if validation_error is not None:
        return validation_error

    sender = _bridge_string(raw_body.get("sender"), max_len=128)
    event_type = _bridge_string(raw_body.get("type"), max_len=32)
    if not sender or event_type not in {"coord", "bar_state", "bar_destroyed", "bar_ctrl", "surface_state"}:
        return JSONResponse(content={"success": False, "error": "invalid_bridge_event"}, status_code=400)

    payload = _sanitize_bridge_value(raw_body.get("payload") if isinstance(raw_body.get("payload"), dict) else {})
    surface_payload = raw_body.get("surface") if isinstance(raw_body.get("surface"), dict) else {}

    global _music_player_owner, _music_player_playback_id, _music_player_latest_state, _music_player_latest_state_expire_at
    async with _music_player_bridge_lock:
        now = time.time()
        _purge_music_player_bridge(now)
        surface = _normalize_music_surface(sender, surface_payload, now)
        if event_type == "surface_state":
            surface = _normalize_music_surface(sender, payload, now)
        _music_player_surfaces[sender] = surface

        if event_type == "coord":
            coord_type = _bridge_string(payload.get("coordType"), max_len=32)
            playback_id = _bridge_string(payload.get("playbackId"), max_len=512)
            if coord_type == "music_pending":
                _music_player_busy_until[sender] = now + _MUSIC_PLAYER_PENDING_TTL_SECONDS
            elif coord_type in {"music_started", "music_heartbeat"}:
                _music_player_busy_until[sender] = now + _MUSIC_PLAYER_ACTIVE_TTL_SECONDS
                if coord_type == "music_started" and _music_player_owner == sender and playback_id:
                    _music_player_playback_id = playback_id
            elif coord_type == "music_ended":
                cleanup_matches = _music_player_owner != sender or _music_player_playback_matches(playback_id)
                if cleanup_matches:
                    _music_player_busy_until.pop(sender, None)
                if _music_player_owner == sender and cleanup_matches:
                    _music_player_latest_state = None
                    _music_player_latest_state_expire_at = 0.0
                    _music_player_owner = None
                    _music_player_playback_id = None
        elif event_type == "bar_state":
            playback_id = _bridge_string(payload.get("playbackId"), max_len=512)
            _music_player_owner = sender
            _music_player_playback_id = playback_id or None
            _music_player_latest_state = payload
            _music_player_latest_state_expire_at = now + _MUSIC_PLAYER_STATE_TTL_SECONDS
            _music_player_busy_until[sender] = now + _MUSIC_PLAYER_STATE_TTL_SECONDS
        elif event_type == "bar_destroyed":
            playback_id = _bridge_string(payload.get("playbackId"), max_len=512)
            cleanup_matches = _music_player_owner != sender or _music_player_playback_matches(playback_id)
            if cleanup_matches:
                _music_player_busy_until.pop(sender, None)
            if _music_player_owner == sender and cleanup_matches:
                _music_player_latest_state = None
                _music_player_latest_state_expire_at = 0.0
                _music_player_owner = None
                _music_player_playback_id = None

        seq = _append_music_player_bridge_event(event_type, sender, payload, surface, now)
        return {"success": True, "seq": seq}


@router.get("/api/music/player/bridge")
async def get_music_player_bridge(
    sender: str = Query(default="", max_length=128),
    since: int = Query(default=0, ge=0),
):
    sender = _bridge_string(sender, max_len=128)
    async with _music_player_bridge_lock:
        now = time.time()
        _purge_music_player_bridge(now)
        events = [
            _public_music_player_bridge_event(event)
            for event in _music_player_bridge_events
            if int(event.get("seq") or 0) > since and event.get("sender") != sender
        ]
        latest_state = _music_player_latest_state if _music_player_latest_state_expire_at > now else None
        owner = _music_player_owner if latest_state is not None else None
        return {
            "success": True,
            "seq": _music_player_bridge_seq,
            "events": events[-80:],
            "active_surface": _choose_music_player_active_surface(now),
            "owner": owner,
            "latest_state": latest_state,
            "remote_music_active": any(
                peer != sender and expire_at > now
                for peer, expire_at in _music_player_busy_until.items()
            ),
        }

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
    Generic music proxy that works around CORS and Referer restrictions.
    - <10MB: fully cached, fast response
    - ≥10MB: streamed, play while downloading
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
    """Streaming generator: creates an independent client internally and handles redirects and streamed reads itself."""
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
        logger.warning("[音乐播放] pyncm_async 不可用，直接使用公开外链")
        return RedirectResponse(url=fallback_url)

    try:
        # 加载 Cookie 并同步到 pyncm_async 会话
        cookies = await asyncio.to_thread(load_cookies_from_file, 'netease')
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
