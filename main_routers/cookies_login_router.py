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
Cookies Login Router - Enhanced

Handles authentication-related endpoints with strict validation and
unified logic for credential management.

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

import asyncio
import re
import io
import base64
from typing import Dict, Optional
from urllib.parse import parse_qsl, urlparse

import qrcode
import httpx
from fastapi import APIRouter, Request, HTTPException, status, Depends, Body
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

# 导入底层认证逻辑和常量
from utils.cookies_login import (
    PlatformLoginManager,
    save_cookies_to_file,
    load_cookies_from_file,
    parse_cookie_string,
    COOKIE_FILES,
    get_cookie_key_file,
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# 预编译恶意内容检测正则，避免每次请求时重复编译
SUSPICIOUS_PATTERN = re.compile(
    r'(<script|javascript:|onload=|eval\(|UNION SELECT|\.\./)',
    re.IGNORECASE
)

def verify_local_access(request: Request):
    """🛡️ Defense in depth: block unauthorized access attempts from non-local hosts."""
    client_host = getattr(request.client, "host", None) if request.client else None
    
    allowed_hosts = ["127.0.0.1", "::1", "localhost"]
    
    if client_host not in allowed_hosts:
        logger.warning(f"🚨 拦截到非本地主机的越权访问尝试，来源 IP: {client_host}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Forbidden: 出于安全考虑，凭证管理页面仅限本地主机 (Localhost) 访问。"
        )

router = APIRouter(prefix="/api/auth", tags=["认证管理"], dependencies=[Depends(verify_local_access)])
templates = Jinja2Templates(directory="templates")
login_manager = PlatformLoginManager()

# Only these credential types can back the personal-dynamics proactive source.
# Keep this aligned with ``utils.web_scraper.personal_dynamics``.
PERSONAL_DYNAMIC_PLATFORMS = frozenset({
    "bilibili",
    "douyin",
    "kuaishou",
    "weibo",
    "reddit",
    "twitter",
})

# ============ 0. 数据模型与校验 ============

class CookieSubmit(BaseModel):
    # 限制平台名称仅允许字母、数字和下划线，彻底杜绝路径遍历风险
    platform: str = Field(..., min_length=2, max_length=20, pattern=r"^[a-z0-9_-]+$")
    cookie_string: str = Field(..., min_length=5, max_length=8192)
    encrypt: Optional[bool] = Field(True, description="是否加密存储")

    @field_validator("cookie_string")
    @classmethod
    def check_suspicious_patterns(cls, v: str) -> str:
        """Security hardening: block XSS or SQL injection patterns."""
        if SUSPICIOUS_PATTERN.search(v):
            logger.warning(f"🚨 检测到恶意内容注入尝试！恶意内容注入，length={len(v)}")
            raise ValueError("检测到非法或危险字符，请求已被系统拦截。")
        return v


# ============ 1. 内部辅助逻辑 ============

def validate_platform_fields(platform: str, cookies: Dict[str, str]):
    """Unified sanity validation of core fields for each platform."""
    platform_validations = {
        "netease": ["MUSIC_U"],
        "bilibili": ["SESSDATA"],
        "xhh": ["user_heybox_id", "user_pkey"],
        "douyin": ["sessionid", "ttwid"],
        "kuaishou": ["kuaishou.server.web_st", "userId"], 
        "weibo": ["SUB"],
        "twitter": ["auth_token"],
        "reddit": ["reddit_session"]
    }
    
    if platform in platform_validations:
        required = platform_validations[platform]
        missing = [f for f in required if not cookies.get(f)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"格式错误：未检测到核心字段 {', '.join(missing)}"
            )


# ============ 2. 网页入口 ============

@router.get("/page", response_class=HTMLResponse, summary="凭证管理可视化后台入口")
async def render_auth_page(request: Request):
    """Credential management page (local access only)."""
    return templates.TemplateResponse("cookies_login.html", {"request": request})

# ============ 3. API 核心功能 ============

@router.get("/platforms", summary="获取支持的平台列表")
async def get_supported_platforms():
    try:
        platforms = login_manager.get_supported_platforms()
        return {
            "success": True,
            "data": {
                p: {
                    "name": info["name"],
                    "methods": info["methods"],
                    "default_method": info["default_method"]
                } for p, info in platforms.items()
            }
        }
    except Exception as e:
        logger.error(f"获取平台列表失败: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="获取支持的平台失败")

@router.post("/cookies/save", summary="保存Cookie")
async def save_cookie(data: CookieSubmit):
    try:
        # 1. 验证平台是否支持
        supported_platforms = login_manager.get_supported_platforms()
        if data.platform not in supported_platforms:
            raise HTTPException(status_code=400, detail=f"不支持的平台: {data.platform}")
            
        # 2. 解析与验证
        cookies = parse_cookie_string(data.cookie_string)
        if not cookies:
            raise HTTPException(status_code=400, detail="未提取到有效的键值对，请检查格式")
        
        validate_platform_fields(data.platform, cookies)
        
        # 3. 存储
        encrypt = data.encrypt if data.encrypt is not None else True
        success = await asyncio.to_thread(save_cookies_to_file, data.platform, cookies, encrypt=encrypt)
        
        if success:
            return {
                "success": True,
                "message": f"✅ {data.platform.capitalize()} 凭证已安全保存！",
                "data": {"platform": data.platform, "count": len(cookies), "encrypted": encrypt}
            }
        raise HTTPException(status_code=500, detail="保存失败，请检查服务器 IO 权限")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存失败: {type(e).__name__}")
        logger.debug(f"详细错误: {e}")  # debug 级别记录详情
        raise HTTPException(status_code=500, detail="内部服务器错误")

@router.get("/cookies/status", summary="获取所有平台Cookie状态汇总")
async def get_all_cookies_status():
    """Return cookie presence status for each supported platform (used by the frontend personal-feed feature)."""
    try:
        platforms = login_manager.get_supported_platforms()
        loaded = await asyncio.gather(
            *(asyncio.to_thread(load_cookies_from_file, p) for p in platforms)
        )
        result = {
            platform_key: {
                "has_cookies": bool(cookies),
                "cookies_count": len(cookies) if cookies else 0,
                "supports_personal_dynamic": platform_key in PERSONAL_DYNAMIC_PLATFORMS,
            }
            for platform_key, cookies in zip(platforms, loaded)
        }
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"获取所有 cookie 状态失败: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="获取平台状态失败")

@router.get("/cookies/{platform}", summary="获取平台Cookie状态")
async def get_platform_cookies(platform: str):
    supported = login_manager.get_supported_platforms()
    if platform not in supported:
        raise HTTPException(status_code=400, detail="平台无效")
            
    cookies = await asyncio.to_thread(load_cookies_from_file, platform)
    if not cookies:
        return {"success": True, "data": {"platform": platform, "has_cookies": False}}
            
    return {
        "success": True,
        "data": {
            "platform": platform,
            "has_cookies": True,
            "cookies_count": len(cookies)
        }
    }

@router.delete("/cookies/{platform}", summary="删除平台Cookie")
async def delete_platform_cookies(platform: str):
    supported = login_manager.get_supported_platforms()
    if platform not in supported:
        raise HTTPException(status_code=400, detail="平台无效")
            
    cookie_file = COOKIE_FILES.get(platform)
    
    # 安全检查文件对象是否存在
    if not cookie_file or not cookie_file.exists():
        return {"success": True, "message": f"{platform} 凭证本就不存在"}
            
    # Step 1: 删除 cookie 文件（独立 try/except，失败才返回 500）
    try:
        cookie_file.unlink()
    except Exception as e:
        logger.error(f"删除 cookie 文件失败: {type(e).__name__}")
        logger.debug(f"详细错误: {e}")
        raise HTTPException(status_code=500, detail="删除 cookie 文件失败，请检查系统权限")

    # Step 2: 删除关联密钥文件（独立 try/except，失败不影响 cookie 已删除的结果）
    key_file = get_cookie_key_file(platform)
    if key_file.exists():
        try:
            key_file.unlink()
        except Exception as e:
            logger.error(f"删除密钥文件失败: {type(e).__name__}")
            logger.debug(f"详细错误: {e}")
            return {
                "success": True,
                "message": f"⚠️ {platform.capitalize()} cookie 已删除，但密钥文件删除失败，请手动清理"
            }

    return {"success": True, "message": f"✅ {platform.capitalize()} 凭证已物理粉碎"}

# ============ 4. 兼容性适配 ============

@router.post("/save_cookie", summary="保存Cookie(兼容旧版)")
async def api_save_cookie_legacy(data: CookieSubmit):
    """Eliminate redundancy by delegating to the unified logic."""
    try:  
        result = await save_cookie(data)
        logger.info(f"✅ 兼容版cookies保存成功 | 平台: {data.platform}")
        logger.debug(f"保存结果: {result}")  # debug 级别记录详情
        return {"success": True, "msg": result["message"]}
    except HTTPException as e:
        logger.warning(f"❌ 兼容版cookies保存失败 | 平台: {data.platform} | 错误: {e.detail}")
        logger.debug(f"详细错误: {e}")  # debug 级别记录详情
        return {"success": False, "msg": f"❌ {e.detail}"}
    except Exception as e:
        logger.error(f"❌ 兼容性cookies保存失败 | 平台: {data.platform} | 错误: {type(e).__name__}")
        logger.debug(f"详细错误: {e}")  # debug 级别记录详情
        return {"success": False, "msg": "❌ 系统异常,请稍后尝试"}






@router.get("/get_CanQRLoginList")
async def get_CanQRLoginLists():
    return list(NetworkQRLoginInfo.keys())





def get_nested_value(data: dict, path: str, default=None):
    if not path:
        return data
    value = data
    for key in path.split("."):
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    return value


_XHH_API_BASE = "https://api.xiaoheihe.cn"
_XHH_QR_CREATE_PATH = "/account/get_qrcode_url/"
_XHH_QR_POLL_PATH = "/account/qr_state/"
_XHH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.xiaoheihe.cn/",
    "Accept": "application/json, text/plain, */*",
}


def _render_qrcode_data_url(qrcode_url: str) -> str:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(qrcode_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")


async def _request_xhh_qr(path: str, extra_params: dict | None = None):
    params = login_manager.build_request_params("xhh", path, extra=extra_params)
    async with httpx.AsyncClient(follow_redirects=True, trust_env=True) as client:
        response = await client.get(
            f"{_XHH_API_BASE}{path}",
            params=params,
            headers=_XHH_HEADERS,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="小黑盒返回了无效响应")
    return response, payload


async def _get_xhh_qr_code():
    _, payload = await _request_xhh_qr(_XHH_QR_CREATE_PATH)
    if str(payload.get("status") or "").lower() != "ok":
        raise HTTPException(
            status_code=502,
            detail=str(payload.get("msg") or "小黑盒二维码获取失败"),
        )
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    qrcode_url = str(result.get("qr_url") or "").strip()
    parsed = urlparse(qrcode_url)
    if not qrcode_url or not parsed.query:
        raise HTTPException(status_code=502, detail="小黑盒二维码数据解析失败")
    timeout = int(result.get("expire") or 180)
    return {
        "success": True,
        "data": {
            "qrcode_key": parsed.query,
            "qrcode_url": qrcode_url,
            "qrcode_image": _render_qrcode_data_url(qrcode_url),
            "timeout": timeout,
        },
    }


async def _poll_xhh_qr_login(qrcode_key: str):
    state_params = dict(parse_qsl(qrcode_key, keep_blank_values=True))
    if not state_params:
        raise HTTPException(status_code=400, detail="小黑盒二维码状态参数无效")
    response, payload = await _request_xhh_qr(_XHH_QR_POLL_PATH, state_params)
    if str(payload.get("status") or "").lower() != "ok":
        raise HTTPException(
            status_code=502,
            detail=str(payload.get("msg") or payload.get("message") or "小黑盒登录状态查询失败"),
        )
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    error = str(result.get("error") or "").strip()
    message = str(result.get("error_msg") or payload.get("msg") or "等待扫码").strip()
    if error.lower() != "ok":
        normalized = f"{error} {message}".lower()
        if any(marker in normalized for marker in ("expired", "expire", "过期", "失效")):
            qr_status = "expired"
        elif any(marker in normalized for marker in ("scanned", "已扫码", "确认")):
            qr_status = "scanned"
        else:
            qr_status = "waiting"
        return {
            "success": False,
            "data": {"code": error or "waiting", "status": qr_status, "message": message},
        }

    cookies = {str(key): str(value) for key, value in response.cookies.items() if value}
    if not cookies:
        raise HTTPException(status_code=502, detail="小黑盒扫码成功但未返回登录凭证")
    validate_platform_fields("xhh", cookies)
    try:
        save_ok = await asyncio.to_thread(save_cookies_to_file, "xhh", cookies)
    except Exception as save_error:
        save_ok = False
        logger.warning(f"⚠️ 小黑盒登录凭证自动保存异常 (不影响登录): {type(save_error).__name__}")
    else:
        if not save_ok:
            logger.warning("⚠️ 小黑盒登录凭证自动保存失败 (不影响登录)")
    return {
        "success": True,
        "data": {
            "code": "ok",
            "status": "success",
            "message": str(result.get("nickname") or "登录成功"),
            "cookies": cookies,
            "cookie_fields": NetworkQRLoginInfo["xhh"]["cookie_fields"],
            "cookies_count": len(cookies),
            "local_save_failed": not save_ok,
        },
    }


@router.post("/get_QR", summary="获取登陆二维码")
async def api_get_qr_code(
    platform: str = Body(..., min_length=2, max_length=20, pattern=r"^[a-zA-Z0-9_-]+$", embed=True)
):
    if platform not in NetworkQRLoginInfo:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")

    if platform == "xhh":
        return await _get_xhh_qr_code()
    
    config = NetworkQRLoginInfo[platform]
    response_config = config.get("response", {})
    
    try:
        import time
        ts = str(int(time.time() * 1000))
        # per-call AsyncClient: 用户扫码登录触发，冷路径（且每次访问外部平台 host 不同）
        async with httpx.AsyncClient() as client:
            req_method = config.get("method", "GET").upper()
            raw_get_params = config.get("get_params", {})
            # 处理动态参数插入
            req_data = {k: (v.replace("{{timestamp}}", ts) if isinstance(v, str) else v) for k, v in raw_get_params.items()}

            if req_method == "POST":
                response = await client.post(url=config["get"], headers=config["headers"], data=req_data, timeout=10)
            else:
                response = await client.get(url=config["get"], headers=config["headers"], params=req_data, timeout=10)
            response.raise_for_status()
            resp_data = response.json()
        
        success_code = response_config.get("success_code", 0)
        if resp_data.get("code") != success_code:
            raise HTTPException(status_code=500, detail="获取二维码失败")
        
        data_path = response_config.get("data_path", "data")
        data = get_nested_value(resp_data, data_path, {})
        
        qrcode_key_path = response_config.get("qrcode_key_path", "qrcode_key")
        qrcode_url_path = response_config.get("qrcode_url_path", "url")
        
        qrcode_key = get_nested_value(data, qrcode_key_path) if "." in qrcode_key_path else data.get(qrcode_key_path)
        qrcode_url = get_nested_value(data, qrcode_url_path) if "." in qrcode_url_path else data.get(qrcode_url_path)
        
        # 兼容自定义拼接，例如网易云的二维码 URL 需要直接用 key 去拼
        url_template = response_config.get("qrcode_url_template")
        if url_template and qrcode_key:
            qrcode_url = url_template.replace("{{qrcode_key}}", str(qrcode_key))
        
        if not qrcode_key or not qrcode_url:
            raise HTTPException(status_code=500, detail="二维码数据解析失败")
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4
        )
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        logger.info(f"✅ {platform} 二维码生成成功")
        
        return {
            "success": True,
            "data": {
                "qrcode_key": qrcode_key,
                "qrcode_url": qrcode_url,
                "qrcode_image": f"data:image/png;base64,{img_base64}",
                "timeout": config.get("timeout", 180)
            }
        }
    except httpx.HTTPError as e:
        logger.error(f"获取二维码网络请求失败: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="网络请求失败，请稍后重试")
    except Exception as e:
        logger.error(f"获取二维码失败: {type(e).__name__}")
        logger.debug(f"详细错误: {e}")
        raise HTTPException(status_code=500, detail="获取二维码失败")

@router.post("/QRLogin", summary="二维码登陆")
async def api_qr_login_poll(
    platform: str = Body(..., min_length=2, max_length=20, pattern=r"^[a-zA-Z0-9_-]+$", embed=True),
    qrcode_key: str = Body(..., min_length=3, max_length=100, embed=True)
):
    if platform not in NetworkQRLoginInfo:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")

    if platform == "xhh":
        return await _poll_xhh_qr_login(qrcode_key)
    
    config = NetworkQRLoginInfo[platform]
    response_config = config.get("response", {})
    status_codes = config.get("status_codes", {})
    cookie_fields = config.get("cookie_fields", [])
    
    try:
        import time
        ts = str(int(time.time() * 1000))
        req_method = config.get("method", "GET").upper()
        # 处理动态参数插入
        raw_poll_params = config.get("poll_params", {"qrcode_key": "{{qrcode_key}}"})
        processed_params = {k: (v.replace("{{qrcode_key}}", qrcode_key).replace("{{timestamp}}", ts) if isinstance(v, str) else v) for k, v in raw_poll_params.items()}

        # per-call AsyncClient: 扫码轮询登录，用户触发冷路径
        async with httpx.AsyncClient() as client:
            if req_method == "POST":
                response = await client.post(
                    url=config["login"],
                    data=processed_params,
                    headers=config["headers"],
                    timeout=10
                )
            else:
                response = await client.get(
                    url=config["login"], 
                    params=processed_params, 
                    headers=config["headers"],
                    timeout=10
                )
            resp_data = response.json()
        
        poll_code_path = response_config.get("poll_code_path", "data.code")
        poll_message_path = response_config.get("poll_message_path", "data.message")
        
        code = get_nested_value(resp_data, poll_code_path)
        raw_message = get_nested_value(resp_data, poll_message_path, "")
        
        if code is None:
            raise HTTPException(status_code=500, detail="HTTP响应数据解析失败")
        
        status_info = status_codes.get(code, {"status": "unknown", "message": raw_message})
        
        # 动态匹配各平台的成功码，不再硬编码 code == 0
        # 例如：Bilibili 成功码为 0
        if status_info.get("status") == "success":
            # 兼容性设计：优先从响应头提取，若 JSON Body 中包含 cookie 字段则解析合并
            cookies = dict(response.cookies)
            body_cookie_str = resp_data.get("cookie")
            if body_cookie_str:
                try:
                    body_cookies = parse_cookie_string(body_cookie_str)
                    cookies.update(body_cookies)
                except Exception as parse_err:
                    logger.warning(f"⚠️ 解析响应体中的 Cookie 字符串失败: {parse_err}")

            cookie_string = "; ".join([f"{k}={v}" for k, v in cookies.items()])
            
            logger.info(f"✅ {platform} 二维码登录成功 (code={code})")
            
            # QR 扫码成功后，自动将 Cookies 持久化到本地文件
            # 避免用户扫码后忘记点击"保存配置"导致 Cookie 丢失
            if cookies and cookie_fields:
                filtered_cookies = {k: v for k, v in cookies.items() if k in cookie_fields}
                if filtered_cookies:
                    save_ok = await asyncio.to_thread(save_cookies_to_file, platform, filtered_cookies)
                    if save_ok:
                        logger.info(f"✅ {platform} QR 登录凭证已自动持久化")
                    else:
                        logger.warning(f"⚠️ {platform} QR 登录凭证自动保存失败 (不影响登录)")
            
            ret = {
                "success": True,
                "data": {
                    "code": code,
                    "status": status_info["status"],
                    "message": status_info["message"],
                    "cookies": cookies,
                    "cookie_string": cookie_string,
                    "cookie_fields": cookie_fields
                }
            }
            return ret
            
        else:
            ret = {
                "success": False,
                "data": {
                    "code": code,
                    "status": status_info["status"],
                    "message": status_info["message"]
                }
            }
            return ret
    except httpx.HTTPError as e:
        logger.error(f"轮询登录状态网络请求失败: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="网络请求失败")
    except Exception as e:
        logger.error(f"轮询登录状态失败: {type(e).__name__}")
        logger.debug(f"详细错误: {e}")
        raise HTTPException(status_code=500, detail="轮询登录状态失败")

#存在用于直接复制后替换内容的方便示例,不需要检查NetworkQRLoginInfo["示例"]内部的东西
NetworkQRLoginInfo = {
    "xhh": {
        "get": f"{_XHH_API_BASE}{_XHH_QR_CREATE_PATH}",
        "login": f"{_XHH_API_BASE}{_XHH_QR_POLL_PATH}",
        "timeout": 180,
        "cookie_fields": ["user_heybox_id", "user_pkey"],
        "headers": _XHH_HEADERS,
        "response": {},
        "status_codes": {},
    },
    "bilibili": {
        "get": "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
        "login": "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
        "timeout": 180,
        "cookie_fields": ["SESSDATA", "bili_jct", "DedeUserID", "buvid3"],
        "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://passport.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin"
            },
        "response": {
            "success_code": 0,
            "data_path": "data",
            "qrcode_key_path": "qrcode_key",
            "qrcode_url_path": "url",
            "poll_code_path": "data.code",
            "poll_message_path": "data.message"
        },
        "status_codes": {
            0: {"status": "success", "message": "登录成功"},
            86101: {"status": "waiting", "message": "未扫码"},
            86090: {"status": "scanned", "message": "已扫码,等待确认"},
            86038: {"status": "expired", "message": "二维码已失效"}
        }
    },
    "示例": {
        "get": "获取二维码的地址",
        "login": "登录的地址",
        "timeout": "二维码有效期(秒):int",
        "cookie_fields": ["需要提取的cookie字段1", "需要提取的cookie字段2", "需要提取的cookie字段n"],
        "headers": "以字典的形式在这里塞一个请求头:dict",
        "response": {
                "success_code": "成功状态:int",
                "data_path": "返回结果JSON中数据的路径",
                "qrcode_key_path": "数据中key的字段名",
                "qrcode_url_path": "数据中URL的字段名",
                "poll_code_path": "轮询中状态码的路径",
                "poll_message_path": "轮询响应中msg的路径"
            },
        "status_codes": {
                0: {"status": "成功登录", "message": "显示的消息"},
                86101: {"status": "等待扫码", "message": "显示的消息"},
                86090: {"status": "已扫码,待确认", "message": "显示的消息"},
                86038: {"status": "二维码过期", "message": "显示的消息"}
            }
    },
}
