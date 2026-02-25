# -*- coding: utf-8 -*-
"""
Cookies Login Router - Enhanced

Handles authentication-related endpoints with strict validation and 
unified logic for credential management.
"""

import re
from typing import Dict, Optional

from fastapi import APIRouter, Request, HTTPException, status, Depends
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
    CONFIG_DIR
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# 预编译恶意内容检测正则，避免每次请求时重复编译
SUSPICIOUS_PATTERN = re.compile(
    r'(<script|javascript:|onload=|eval\(|UNION SELECT|\.\./)',
    re.IGNORECASE
)

def verify_local_access(request: Request):
    """🛡️ 纵深防御：拦截非本地主机的越权访问尝试"""
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

# ============ 0. 数据模型与校验 ============

class CookieSubmit(BaseModel):
    # 限制平台名称仅允许字母、数字和下划线，彻底杜绝路径遍历风险
    platform: str = Field(..., min_length=2, max_length=20, pattern=r"^[a-z0-9_-]+$")
    cookie_string: str = Field(..., min_length=5, max_length=8192)
    encrypt: Optional[bool] = Field(True, description="是否加密存储")

    @field_validator("cookie_string")
    @classmethod
    def check_suspicious_patterns(cls, v: str) -> str:
        """安全加固：拦截 XSS 或 SQL 注入特征"""
        if SUSPICIOUS_PATTERN.search(v):
            logger.warning(f"🚨 检测到恶意内容注入尝试！恶意内容注入，length={len(v)}")
            raise ValueError("检测到非法或危险字符，请求已被系统拦截。")
        return v

# ============ 1. 内部辅助逻辑 ============

def validate_platform_fields(platform: str, cookies: Dict[str, str]):
    """统一的各平台核心字段防呆校验"""
    platform_validations = {
        "bilibili": ["SESSDATA"],
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
    """访问凭证管理网页 (限制仅本地访问)"""
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
        success = save_cookies_to_file(data.platform, cookies, encrypt=encrypt)
        
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
    """返回每个支持平台的 Cookie 存在状态（前端个人动态功能使用）"""
    try:
        platforms = login_manager.get_supported_platforms()
        result = {}
        for platform_key in platforms:
            cookies = load_cookies_from_file(platform_key)
            result[platform_key] = {
                "has_cookies": bool(cookies),
                "cookies_count": len(cookies) if cookies else 0,
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
            
    cookies = load_cookies_from_file(platform)
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
    key_file = CONFIG_DIR / f"{platform}_key.key"
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
    """通过调用统一逻辑来消除冗余"""
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
