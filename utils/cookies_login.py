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
Unified cookie login and credential management module (security-hardened edition)
=========================================================
Fetches and saves authentication cookies for each platform, with system-level protections.

[Core security features]
1. Credential masking: core tokens are masked in both terminal input and log records.
2. System-level file lock: after saving plaintext JSON, file permissions are locked automatically (owner read/write only, 0o600).
3. Credential validity check: before saving, mandatory check that platform core fields (e.g. SESSDATA, SUB) are present.
4. Deep environment camouflage: full Origin/Referer request headers to avoid triggering account-environment risk control.
"""

import json
import os
import sys
from typing import Dict, Any, Optional
from pathlib import Path
import logging

from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

# ==========================================
# 基础配置与日志
# ==========================================
logger = get_module_logger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

CONFIG_DIR = Path("config")
COOKIE_FILES = {
    'netease': CONFIG_DIR / 'netease_cookies.json',
    'bilibili': CONFIG_DIR / 'bilibili_cookies.json',
    "douyin": CONFIG_DIR / 'douyin_cookies.json',
    "kuaishou": CONFIG_DIR / 'kuaishou_cookies.json', 
    'weibo': CONFIG_DIR / 'weibo_cookies.json',
    'reddit': CONFIG_DIR / 'reddit_cookies.json',
    'twitter': CONFIG_DIR / 'twitter_cookies.json',
    'youtube': CONFIG_DIR / 'youtube_cookies.json'
}

class LoginStatus:
    SUCCESS = 0
    FAILED = -1
    TIMEOUT = -2

# ==========================================
# 🛡️ 安全模块：脱敏、校验与文件锁
# ==========================================
def mask_string(s: str) -> str:
    """Mask sensitive credentials to prevent shoulder-surfing or log leaks"""
    if not s:
        return ""
    if len(s) < 8:
        return "***"
    return f"{s[:4]}...{s[-4:]}"

def validate_cookies(platform: str, cookies: Dict[str, str]) -> bool:
    """Core credential integrity check, preventing incomplete cookies from causing account anomalies or risk control"""
    if platform == 'youtube':
        if not cookies.get('SAPISID'):
            logger.warning("⚠️ 安全拦截：YouTube Cookie 缺少 SAPISID！")
            return False
        return True

    required_keys = {
        'netease': ['MUSIC_U'],
        'bilibili': ['SESSDATA'],
        "douyin": ['sessionid', 'ttwid'],
        "kuaishou": ['kuaishou.server.web_st', 'userId'], 
        'weibo': ['SUB'],
        'twitter': ['auth_token']
        # reddit Cookie 变动较大，暂不做强制硬性校验
    }
    
    if platform in required_keys:
        for key in required_keys[platform]:
            if key not in cookies or not cookies[key]:
                logger.warning(f"⚠️ 安全拦截：提取的 Cookie 中缺失核心字段 '{key}'！")
                return False
    return True

def save_cookies_to_file(platform: str, cookies: Dict[str, Any], encrypt: bool = True) -> bool:
    """Save cookies, with normalization checks and encryption logic"""
    try:
        if platform not in COOKIE_FILES:
            return False

        # 【核心修复】增加防御性调用，确保即便从程序化接口传入的 dict 也能通过值类型校验
        cookies = _normalize_cookies(cookies, platform)
        if not cookies:
            return False
            
        if not validate_cookies(platform, cookies):
            logger.error(f"❌ 凭证核心字段校验失败，{platform} Cookie 保存已取消。")
            return False
            
        cookie_file = COOKIE_FILES[platform]
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform != 'win32':
           os.chmod(CONFIG_DIR, 0o700)  # 仅所有者可访问
        
        # 根据参数决定是否加密
        if encrypt:
            # 加密保存
            from cryptography.fernet import Fernet
            
            # 生成或加载加密密钥
            key_file = CONFIG_DIR / f"{platform}_key.key"
            if key_file.exists():
                with open(key_file, 'rb') as f:
                    key = f.read()
            else:
                key = Fernet.generate_key()
                with open(key_file, 'wb') as f:
                    f.write(key)
                # 设置密钥文件权限
                if sys.platform != 'win32':
                    os.chmod(key_file, 0o600)
            
            # 加密Cookie数据
            fernet = Fernet(key)
            cookie_json = json.dumps(cookies, ensure_ascii=False)
            encrypted_data = fernet.encrypt(cookie_json.encode('utf-8'))
            
            # 保存加密数据
            with open(cookie_file, 'wb') as f:
                f.write(encrypted_data)
            
            # 设置Cookie文件权限
            if sys.platform != 'win32':
                os.chmod(cookie_file, 0o600)
            
            logger.info(f"✅ 已加密保存 {platform} 凭证到: {cookie_file}")
        else:
            # 明文保存
            atomic_write_json(cookie_file, cookies, ensure_ascii=False, indent=4)
                
            # 🔒 安全加固：修改文件权限为 600 (仅当前用户可读写)，防止跨用户窃取
            if sys.platform != 'win32':
                os.chmod(cookie_file, 0o600)
            
            logger.info(f"✅ 已明文保存 {platform} 凭证到: {cookie_file}")
        
        logger.info(f"🔐 【{platform.capitalize()} 凭证摘要】:")
        for k, v in list(cookies.items())[:3]: # 仅展示前三个键
            logger.info(f"   - {k}: {mask_string(v)}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 保存 Cookie 失败: {e}")
        return False

def _normalize_cookies(cookies: Dict[str, Any], platform: str) -> Dict[str, str]:
    """
    Normalize the cookie structure:
    - Require all keys and values to be strings
    - Prevent int/bool/None and other non-string values from being accidentally converted to non-empty strings (e.g. "False")
    """
    valid_cookies: Dict[str, str] = {}
    
    for k, v in cookies.items():
        # 1. 校验键必须为字符串
        if not isinstance(k, str):
            logger.warning(f"[{platform}] Cookie 键格式错误：'{k}' 必须为字符串类型")
            return {}
        
        # 2. 【核心修复】校验值必须为字符串。
        # 移除对 dict/list/int/bool 的分段判断，统一实行“非字符即非法”策略
        if isinstance(v, str):
            valid_cookies[k] = v
        else:
            logger.warning(f"[{platform}] Cookie 格式非法：键 '{k}' 的值必须为字符串，当前类型为 {type(v).__name__}")
            # 只要发现一个字段不是字符串（如 bool、int 或 None），即认为整组凭证无效，防止绕过验证
            return {}
    
    return valid_cookies

def load_cookies_from_file(platform: str) -> Dict[str, str]:
    """Load cookies from file, auto-detecting whether they are encrypted"""
    try:
        if platform not in COOKIE_FILES:
            return {}
            
        cookie_file = COOKIE_FILES[platform]
        if not cookie_file.exists():
            return {}
        
        # 尝试解密加载
        try:
            from cryptography.fernet import Fernet
            
            # 加载加密密钥
            key_file = CONFIG_DIR / f"{platform}_key.key"
            if key_file.exists():
                with open(key_file, 'rb') as f:
                    key = f.read()
                
                # 解密Cookie数据
                with open(cookie_file, 'rb') as f:
                    encrypted_data = f.read()
                
                fernet = Fernet(key)
                decrypted_data = fernet.decrypt(encrypted_data).decode('utf-8')
                cookies = json.loads(decrypted_data)
                
                # 校验 Cookie 结构: 确保所有值都是字符串
                if isinstance(cookies, dict):
                    valid_cookies = _normalize_cookies(cookies, platform)
                    # 【新增】判断归一化后是否为空，并进行核心必填字段校验
                    if not valid_cookies or not validate_cookies(platform, valid_cookies):
                        logger.warning(f"{platform} Cookie 解密后核心字段校验不通过，拒绝加载")
                        return {}
                        
                    logger.info(f"✅ 已解密加载 {platform} 凭证")
                    return valid_cookies
                else:
                    logger.warning(f"{platform} Cookie 解密后不是对象")
                    return {}
            else:
                # 密钥文件不存在，可能是明文文件
                raise FileNotFoundError("密钥文件不存在")
                
        except Exception as decrypt_error:
            # 解密失败，尝试明文加载
            logger.debug(f"解密 {platform} Cookie 失败，尝试明文加载: {decrypt_error}")
            
            try:
                # 【清理】移除重复的 exists() 检查，函数入口已做保护
                if cookie_file.stat().st_size == 0:
                    logger.info(f"{platform} Cookie 文件为空: {cookie_file}")
                    return {}
                
                with open(cookie_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:
                        logger.warning(f"{platform} Cookie 文件内容为空或只有空白字符: {cookie_file}")
                        return {}
                
                cookies = json.loads(content)
                if not isinstance(cookies, dict):
                    logger.warning(f"{platform} Cookie 明文内容不是对象: {cookie_file}")
                    return {}
                
                # 校验 Cookie 结构：确保所有值都是字符串
                valid_cookies = _normalize_cookies(cookies, platform)
                # 【新增】判断归一化后是否为空（被判定为不合法）
                # 同样补上 validate_cookies 校验
                if not valid_cookies or not validate_cookies(platform, valid_cookies):
                    logger.warning(f"{platform} Cookie 明文内容核心字段校验不通过，拒绝加载: {cookie_file}")
                    return {}
                
                logger.info(f"✅ 已明文加载 {platform} 凭证")
                return valid_cookies
                
            except Exception as plain_error:
                logger.error(f"明文加载 {platform} Cookie 失败: {plain_error}")
                return {}
                
    except Exception as e:
        logger.error(f"❌ 加载 {platform} Cookie 失败: {e}")
        return {}

def parse_cookie_string(cookie_string: str) -> Dict[str, str]:
    """Parse plaintext cookies"""
    cookies = {}
    if not cookie_string:
        return cookies
    for item in cookie_string.split(';'):
        if '=' in item:
            key, value = item.strip().split('=', 1)
            cookies[key.strip()] = value.strip()
    return cookies

 

def get_bilibili_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【B站手动导入】(注意：请勿在此界面外泄露您的 SESSDATA)")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]") 
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('bilibili', cookies)  # noqa: ASYNC_BLOCK — CLI-only path; outer fn already blocks on input()
    return cookies

# ==========================================
# 其他平台登录逻辑 (纯手工导入)
# ==========================================
def get_douyin_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【抖音手动导入】(需包含 sessionid 和 ttwid 字段)")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('douyin', cookies)  # noqa: ASYNC_BLOCK — CLI-only path; outer fn already blocks on input()
    return cookies

def get_kuaishou_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【快手手动导入】(需包含 kuaishou.server.web_st 字段)")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('kuaishou', cookies)  # noqa: ASYNC_BLOCK — CLI-only path; outer fn already blocks on input()
    return cookies

def get_weibo_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【微博手动导入】(需包含 SUB 字段)")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('weibo', cookies)  # noqa: ASYNC_BLOCK — CLI-only path; outer fn already blocks on input()
    return cookies

def get_reddit_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【Reddit 手动导入】")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('reddit', cookies)  # noqa: ASYNC_BLOCK — CLI-only path; outer fn already blocks on input()
    return cookies

def get_twitter_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【Twitter/X 手动导入】")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('twitter', cookies)  # noqa: ASYNC_BLOCK — CLI-only path; outer fn already blocks on input()
    return cookies

def get_youtube_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【YouTube 手动导入】(必须包含 SAPISID 字段)")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('youtube', cookies)
    return cookies

def get_netease_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【网易云音乐手动导入】(需包含 MUSIC_U 字段)")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('netease', cookies)  # noqa: ASYNC_BLOCK — CLI-only path; outer fn already blocks on input()
    return cookies

# ==========================================
# 交互式终端 UI 引擎
# ==========================================
class PlatformLoginManager:
    def __init__(self):
        self.platforms = {
            'netease': {'name': '网易云音乐', 'methods': ['manual'], 'func': get_netease_cookies},
            'bilibili': {'name': 'Bilibili', 'methods': ['manual'], 'func': get_bilibili_cookies},
            "douyin": {'name': '抖音', 'methods': ['manual'], 'func': get_douyin_cookies},
            "kuaishou": {'name': '快手', 'methods': ['manual'], 'func': get_kuaishou_cookies},
            'weibo': {'name': '微博', 'methods': ['manual'], 'func': get_weibo_cookies},
            'reddit': {'name': 'Reddit', 'methods': ['manual'], 'func': get_reddit_cookies},
            'twitter': {'name': 'Twitter/X', 'methods': ['manual'], 'func': get_twitter_cookies},
            'youtube': {'name': 'YouTube', 'methods': ['manual'], 'func': get_youtube_cookies}
        }
    
    def login_platform(self, platform: str, method: str) -> Optional[Dict[str, str]]:
        if platform in self.platforms:
            return self.platforms[platform]['func'](method)
        return None
    
    def get_supported_platforms(self) -> Dict[str, Dict[str, Any]]:
        """Get supported platforms and their login methods"""
        result = {}
        for platform, info in self.platforms.items():
            result[platform] = {
                "name": info['name'],
                "methods": info['methods'],
            }
            if info['methods']:
                result[platform]['default_method'] = info['methods'][0]
            else:
                result[platform]['default_method'] = None
        return result

def interactive_login():
    manager = PlatformLoginManager()
    platforms = list(manager.platforms.items())
    
    while True:
        print("\n" + "=" * 45)
        print("🌟 N.E.K.O 安全凭证管理终端 (Security V2) 🌟")
        print("=" * 45)
        for i, (key, info) in enumerate(platforms, 1):
            methods_str = '/'.join(info['methods'])
            print(f"  [{i}] {info['name'].ljust(12)} (支持: {methods_str})")
        print("  [0] 退出程序")
        print("=" * 45)
        
        max_idx = len(platforms)
        choice = input(f"👉 请选择要配置的平台 (0-{max_idx}): ").strip()
        if choice == "0":
            print("👋 凭证管理已安全退出。")
            break
            
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(platforms):
                p_key, p_info = platforms[idx]
                
                method = p_info['methods'][0]
                if len(p_info['methods']) > 1:
                    print(f"\n请选择 {p_info['name']} 的验证方式:")
                    for j, m in enumerate(p_info['methods'], 1):
                        print(f"[{j}] {m}")
                    m_choice = input("👉 选择 (默认1): ").strip()
                    try:
                        m_idx = int(m_choice) - 1
                        if 0 <= m_idx < len(p_info['methods']):
                            method = p_info['methods'][m_idx]
                    except ValueError:
                        pass
                
                print(f"\n🚀 正在启动 {p_info['name']} 的 {method} 安全流程...")
                manager.login_platform(p_key, method)
            else:
                print("❌ 无效的序号。")
        except ValueError:
            print("❌ 请输入数字。")
        except KeyboardInterrupt:
            print("\n👋 强制退出流程。")
            break

if __name__ == "__main__":
    try:
        interactive_login()
    except KeyboardInterrupt:
        print("\n👋 终端已安全关闭。")
        sys.exit(0)
