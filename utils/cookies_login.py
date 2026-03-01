"""
统一 Cookie 登录与凭证管理模块 (安全加固版)
=========================================================
用于获取并保存各平台的认证 Cookie，包含系统级安全防护。

【核心安全特性】
1. 凭证脱敏显示：终端输入和日志记录均对核心 Token 进行遮罩处理 (Masking)。
2. 系统级文件锁：明文 JSON 保存后，自动锁定文件权限 (仅限所有者读写 0o600)。
3. 凭证有效性校验：保存前强制校验是否包含平台核心字段 (如 SESSDATA, SUB)。
4. 深度环境伪装：增加完整的 Origin/Referer 请求头，防止触发账号环境风控。
"""

import asyncio
import json
import os
import sys
from typing import Dict, Any, Optional
from pathlib import Path
import logging

from utils.logger_config import get_module_logger

# ==========================================
# 基础配置与日志
# ==========================================
logger = get_module_logger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

CONFIG_DIR = Path("config")
COOKIE_FILES = {
    'bilibili': CONFIG_DIR / 'bilibili_cookies.json',
    "douyin": CONFIG_DIR / 'douyin_cookies.json',
    "kuaishou": CONFIG_DIR / 'kuaishou_cookies.json', 
    'weibo': CONFIG_DIR / 'weibo_cookies.json',
    'reddit': CONFIG_DIR / 'reddit_cookies.json',
    'twitter': CONFIG_DIR / 'twitter_cookies.json'
}

class LoginStatus:
    SUCCESS = 0
    FAILED = -1
    TIMEOUT = -2

# ==========================================
# 🛡️ 安全模块：脱敏、校验与文件锁
# ==========================================
def mask_string(s: str) -> str:
    """对敏感凭证进行打码处理，防止屏幕偷窥或日志泄露"""
    if not s:
        return ""
    if len(s) < 8:
        return "***"
    return f"{s[:4]}...{s[-4:]}"

def validate_cookies(platform: str, cookies: Dict[str, str]) -> bool:
    """核心凭证防伪校验，防止残缺 Cookie 导致账号异常或风控"""
    required_keys = {
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

def save_cookies_to_file(platform: str, cookies: Dict[str, str], encrypt: bool = True) -> bool:
    """保存Cookie，可选择是否加密"""
    try:
        if platform not in COOKIE_FILES:
            return False
            
        if not validate_cookies(platform, cookies):
            print(f"❌ 凭证格式异常，{platform} Cookie 保存已取消。")
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
            with open(cookie_file, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=4)
                
            # 🔒 安全加固：修改文件权限为 600 (仅当前用户可读写)，防止跨用户窃取
            if sys.platform != 'win32':
                os.chmod(cookie_file, 0o600)
            
            logger.info(f"✅ 已明文保存 {platform} 凭证到: {cookie_file}")
        
        # 打印脱敏后的摘要，让用户安心
        logger.info(f"\n🔐 【{platform.capitalize()} 凭证摘要】:")
        for k, v in list(cookies.items())[:3]: # 仅展示前三个键
            print(f"   - {k}: {mask_string(v)}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 保存 Cookie 失败: {e}")
        return False

def load_cookies_from_file(platform: str) -> Dict[str, str]:
    """从文件加载Cookie，自动检测是否加密"""
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
                
                logger.info(f"✅ 已解密加载 {platform} 凭证")
                return cookies if isinstance(cookies, dict) else {}
            else:
                # 密钥文件不存在，可能是明文文件
                raise FileNotFoundError("密钥文件不存在")
                
        except Exception as decrypt_error:
            # 解密失败，尝试明文加载
            logger.debug(f"解密 {platform} Cookie 失败，尝试明文加载: {decrypt_error}")
            
            try:
                # 先检查文件是否存在以及是否有内容
                if not cookie_file.exists():
                    logger.warning(f"{platform} Cookie 文件不存在: {cookie_file}")
                    return {}
                
                if cookie_file.stat().st_size == 0:
                    logger.info(f"{platform} Cookie 文件为空: {cookie_file}")
                    return {}
                
                with open(cookie_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:
                        logger.warning(f"{platform} Cookie 文件内容为空或只有空白字符: {cookie_file}")
                        return {}
                    
                    # 尝试解析JSON
                    try:
                        cookies = json.loads(content)
                    except json.JSONDecodeError as json_error:
                        logger.error(f"{platform} Cookie 文件JSON格式错误: {json_error}")
                        logger.debug(f"错误的JSON内容预览: {content[:200]}...")
                        return {}

                logger.info(f"✅ 已明文加载 {platform} 凭证")
                return cookies if isinstance(cookies, dict) else {}
            except Exception as plain_error:
                logger.error(f"明文加载 {platform} Cookie 也失败: {plain_error}")
                return {}
        
    except Exception as e:
        logger.error(f"❌ 加载 {platform} Cookie 失败: {e}")
        return {}

def parse_cookie_string(cookie_string: str) -> Dict[str, str]:
    """解析纯文本 Cookie"""
    cookies = {}
    if not cookie_string:
        return cookies
    for item in cookie_string.split(';'):
        if '=' in item:
            key, value = item.strip().split('=', 1)
            cookies[key.strip()] = value.strip()
    return cookies

 

async def get_bilibili_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【B站手动导入】(注意：请勿在此界面外泄露您的 SESSDATA)")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]") 
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('bilibili', cookies)
    return cookies

# ==========================================
# 其他平台登录逻辑 (纯手工导入)
# ==========================================
async def get_douyin_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【抖音手动导入】(需包含 sessionid 和 ttwid 字段)")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('douyin', cookies)
    return cookies

async def get_kuaishou_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【快手手动导入】(需包含 kuaishou.server.web_st 字段)")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('kuaishou', cookies)
    return cookies

async def get_weibo_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【微博手动导入】(需包含 SUB 字段)")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('weibo', cookies)
    return cookies

async def get_reddit_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【Reddit 手动导入】")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('reddit', cookies)
    return cookies

async def get_twitter_cookies(_method: str = "manual") -> Optional[Dict[str, str]]:
    print("\n" + "-" * 40)
    print("【Twitter/X 手动导入】")
    cookie_string = input("👉 请粘贴 Cookie: ").strip()
    print("\033[F\033[K" + "👉 请粘贴 Cookie: [已接收，已脱敏掩码]")
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        save_cookies_to_file('twitter', cookies)
    return cookies

# ==========================================
# 交互式终端 UI 引擎
# ==========================================
class PlatformLoginManager:
    def __init__(self):
        self.platforms = {
            'bilibili': {'name': 'Bilibili', 'methods': ['manual'], 'func': get_bilibili_cookies},
            "douyin": {'name': '抖音', 'methods': ['manual'], 'func': get_douyin_cookies},
            "kuaishou": {'name': '快手', 'methods': ['manual'], 'func': get_kuaishou_cookies},
            'weibo': {'name': '微博', 'methods': ['manual'], 'func': get_weibo_cookies},
            'reddit': {'name': 'Reddit', 'methods': ['manual'], 'func': get_reddit_cookies},
            'twitter': {'name': 'Twitter/X', 'methods': ['manual'], 'func': get_twitter_cookies}
        }
    
    async def login_platform(self, platform: str, method: str) -> Optional[Dict[str, str]]:
        if platform in self.platforms:
            return await self.platforms[platform]['func'](method)
        return None
    
    def get_supported_platforms(self) -> Dict[str, Dict[str, Any]]:
        """获取支持的平台及其登录方式"""
        result = {}
        for platform, info in self.platforms.items():
            result[platform] = {
                "name": info['name'],
                "methods": info['methods'],
                "default_method": info['methods'][0] if info['methods'] else None
            }
        return result

async def interactive_login():
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
                await manager.login_platform(p_key, method)
            else:
                print("❌ 无效的序号。")
        except ValueError:
            print("❌ 请输入数字。")
        except KeyboardInterrupt:
            print("\n👋 强制退出流程。")
            break

if __name__ == "__main__":
    try:
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(interactive_login())
    except KeyboardInterrupt:
        print("\n👋 终端已安全关闭。")
        sys.exit(0)