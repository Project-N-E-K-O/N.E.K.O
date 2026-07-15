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
"""Platform cookies and Bilibili credential helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import os

# bs4 惰性 import（各解析函数内首用加载，utils.module_warmup 后台预热兜底）：本模块被
# system_router 顶层引用、坐在 main_server 启动 import 链上，顶层 bs4 会拖慢端口就绪。
if TYPE_CHECKING:
    pass
from pathlib import Path
import json

from ._shared import logger

def _get_bilibili_credential() -> Any | None:
    try:
        from bilibili_api import Credential
        cookies = _get_platform_cookies('bilibili')
        if not cookies:
            return None
        
        # 兼容原版逻辑，加入 buvid3 防止被 B站 API 风控拦截
        return Credential(
            sessdata=cookies.get('SESSDATA', ''),
            bili_jct=cookies.get('bili_jct', ''),
            buvid3=cookies.get('buvid3', ''),
            dedeuserid=cookies.get('DedeUserID', '')
        )
    except ImportError:
        logger.debug("bilibili_api 库未安装")
        return None
    except Exception as e:
        logger.debug(f"从文件加载认证信息失败: {e}")
    
    return None

def _get_platform_cookies(platform_name: str) -> dict[str, str]:
    """
    Generic platform cookie reader (hooks into the system's unified encrypted/plaintext read logic)
    """
    try:
        # 优先调用系统底层的解密读取逻辑
        from utils.cookies_login import load_cookies_from_file
        cookies = load_cookies_from_file(platform_name)
        if cookies:
            logger.debug(f"✅ 成功通过底层接口加载 {platform_name} 凭证")
            return cookies
    except Exception as e:
        logger.debug(f"底层接口加载 {platform_name} 凭证失败: {e}，尝试使用明文回退...")

    # 下面是作为回退的明文读取逻辑（兜底处理旧文件）
    possible_paths = [
        Path(os.path.expanduser('~')) / f'{platform_name}_cookies.json',
        Path('config') / f'{platform_name}_cookies.json',
        Path('.') / f'{platform_name}_cookies.json',
    ]
    
    for cookie_file in possible_paths:
        if not cookie_file.exists():
            continue
            
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                cookie_data = json.load(f)

            cookies = {}
            if isinstance(cookie_data, list):
                for cookie in cookie_data:
                    name, value = cookie.get('name'), cookie.get('value')
                    if name and value: 
                        cookies[name] = value
            elif isinstance(cookie_data, dict):
                cookies = cookie_data
            
            if cookies:
                return cookies
        except Exception:
            continue

    return {}
