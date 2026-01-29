"""
插件服务器认证模块

使用 OAuth2 模式，通过验证码进行鉴权。
验证码在服务器启动时生成并打印到终端。
"""
import secrets
import string
from typing import Optional
from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# 全局验证码存储
_admin_code: Optional[str] = None

# HTTP Bearer 安全方案
security = HTTPBearer(auto_error=False)


def generate_admin_code() -> str:
    """
    生成4个字符的字母验证码
    
    Returns:
        4个字符的大写字母验证码
    """
    return ''.join(secrets.choice(string.ascii_uppercase) for _ in range(4))


def set_admin_code(code: str) -> None:
    """设置管理员验证码"""
    global _admin_code
    _admin_code = code


def get_admin_code() -> Optional[str]:
    """获取当前管理员验证码"""
    return _admin_code


async def verify_admin_code(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> str:
    """
    验证管理员验证码（OAuth2 Bearer Token 模式）
    
    Args:
        credentials: HTTP Bearer 凭证(从 Authorization 头获取)
    
    Returns:
        验证通过时返回 "authenticated"
    
    Raises:
        HTTPException: 如果验证失败
    """
    global _admin_code
    
    if _admin_code is None:
        raise HTTPException(
            status_code=503,
            detail="Authentication not initialized"
        )
    
    # 从 Bearer token 中获取验证码
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    provided_code = credentials.credentials.upper()
    
    if provided_code != _admin_code:
        raise HTTPException(
            status_code=403,
            detail="Invalid authentication code",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return "authenticated"


# 创建依赖项
require_admin = Depends(verify_admin_code)

