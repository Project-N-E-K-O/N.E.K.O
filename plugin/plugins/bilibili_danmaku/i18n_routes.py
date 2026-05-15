from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from plugin.logging_config import get_logger

router = APIRouter(tags=["bilibili-i18n"])
logger = get_logger("bilibili.i18n_routes")


def _is_safe_url(url: str) -> bool:
    """SSRF 防护：只允许公网 HTTP/HTTPS 请求

    检查所有 DNS 解析到的 IP（防止多 A 记录绕过）。
    DNS rebinding（首次解析公网 IP 通过检查，后续重解析指向内网）需结合
    aiohttp TCPConnector 的 resolved_hosts 参数做单次解析绑定才能根除；
    在本插件的使用场景中（用户手动配置的 API URL），现有防护已提供足够
    的防御深度。
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        # 禁止 localhost / 127.0.0.1 / ::1
        if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return False
        # 禁止内网 / 链路本地地址（检查所有解析到的 IP）
        try:
            addrs = socket.getaddrinfo(host, 0)
            for addr_info in addrs:
                addr = ipaddress.ip_address(addr_info[4][0])
                if addr.is_private or addr.is_loopback or addr.is_link_local:
                    return False
        except Exception:
            return False  # 解析失败也拒绝
        return True
    except Exception:
        return False

_I18N_DIR = Path(__file__).resolve().parent / "i18n"
_ALLOWED_LOCALES = {"zh-CN", "en", "ja", "ko", "zh-TW", "ru", "es", "pt"}


class BgLlmTestRequest(BaseModel):
    url: str = ""
    api_key: str = ""
    model: str = ""


@router.get("/plugin/bilibili_danmaku/ui-api/locale")
async def get_bili_locale() -> JSONResponse:
    try:
        from utils.language_utils import get_global_language_full

        locale = str(get_global_language_full() or "zh-CN")
    except Exception:
        locale = "zh-CN"
    return JSONResponse({"locale": _normalize_locale(locale)})


@router.get("/plugin/bilibili_danmaku/ui-api/i18n/{locale}.json")
async def get_bili_i18n(locale: str) -> Response:
    normalized = str(locale or "").strip()
    if ".." in normalized or "/" in normalized or "\\" in normalized:
        return Response(status_code=404)
    if normalized not in _ALLOWED_LOCALES:
        return Response(status_code=404)
    file = _I18N_DIR / f"{normalized}.json"
    if not file.is_file():
        return Response(status_code=404)
    return FileResponse(file)


@router.post("/plugin/bilibili_danmaku/ui-api/test-bg-llm")
async def test_bg_llm(req: BgLlmTestRequest) -> JSONResponse:
    """Test background LLM connectivity by making a minimal chat completion request.

    Mirrors the main project's /api/config/test_connectivity behaviour:
    sends a single-turn "hi" with max_tokens=1 and classifies the response.
    Falls back to saved config.json if form params are empty.
    """
    url = req.url.strip() if req.url else ""
    api_key = req.api_key.strip() if req.api_key else ""
    model = req.model.strip() if req.model else ""

    # Fall back to saved config if form fields are empty
    if not url or not api_key:
        try:
            config_path = Path(__file__).resolve().parent / "data" / "config.json"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                cloud = saved.get("background_llm", {}).get("cloud", {})
                if not url:
                    url = (cloud.get("url") or "").strip()
                if not api_key:
                    api_key = (cloud.get("api_key") or "").strip()
                if not model:
                    model = (cloud.get("model") or "").strip()
        except Exception as exc:
            logger.warning("failed to read saved bg llm config: {}", exc)

    if not url:
        return JSONResponse(
            {"success": False, "error": "请先填写 API 地址", "error_code": "missing_params"}
        )

    # Build full chat/completions URL
    api_url = url.rstrip("/")
    if not api_url.endswith("/chat/completions"):
        if api_url.endswith("/v1"):
            api_url += "/chat/completions"
        else:
            api_url += "/v1/chat/completions"

    # SSRF 防护
    if not _is_safe_url(api_url):
        return JSONResponse(
            {"success": False, "error": "不安全的 API 地址：仅允许公网 HTTP/HTTPS 请求", "error_code": "ssrf_blocked"}
        )

    payload = {
        "model": model or "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    return JSONResponse({"success": True})
                if resp.status in (401, 403):
                    body = await resp.text()
                    return JSONResponse(
                        {"success": False, "error": "API Key 无效或已过期", "error_code": "auth_failed", "detail": body[:300]}
                    )
                body = await resp.text()
                return JSONResponse(
                    {"success": False, "error": f"HTTP {resp.status}", "error_code": "http_error", "detail": body[:300]}
                )
    except asyncio.TimeoutError:
        return JSONResponse({"success": False, "error": "请求超时（10秒）", "error_code": "timeout"})
    except aiohttp.ClientConnectorError as e:
        err_str = str(e).lower()
        if "getaddrinfo" in err_str or "name or service not known" in err_str:
            return JSONResponse({"success": False, "error": "域名解析失败", "error_code": "dns_error"})
        return JSONResponse({"success": False, "error": f"无法连接到目标服务器", "error_code": "connection_refused"})
    except aiohttp.ClientError as e:
        return JSONResponse({"success": False, "error": f"请求失败: {e}", "error_code": "request_error"})
    except Exception as e:
        logger.exception("unexpected error testing bg llm")
        return JSONResponse({"success": False, "error": str(e), "error_code": "unknown"})


def _normalize_locale(locale: str) -> str:
    normalized = str(locale or "").strip().replace("_", "-").lower()
    if normalized == "zh" or normalized.startswith("zh-"):
        if normalized in ("zh-tw", "zh-hk", "zh-mo"):
            return "zh-TW"
        return "zh-CN"
    if normalized.startswith("en"):
        return "en"
    if normalized.startswith("ja"):
        return "ja"
    if normalized.startswith("ko"):
        return "ko"
    if normalized.startswith("ru"):
        return "ru"
    if normalized.startswith("es"):
        return "es"
    if normalized.startswith("pt"):
        return "pt"
    return "zh-CN"
