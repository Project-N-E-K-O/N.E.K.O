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
"""Platform credentials and request protocol helpers."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import TYPE_CHECKING, Any
import os

# bs4 惰性 import（各解析函数内首用加载，utils.module_warmup 后台预热兜底）：本模块被
# system_router 顶层引用、坐在 main_server 启动 import 链上，顶层 bs4 会拖慢端口就绪。
if TYPE_CHECKING:
    pass
from pathlib import Path
import json

from ._shared import logger


_XHH_SIGNING_KEY = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
_XHH_TOKEN_PHRASES = ("唉？！云朵！", "哒哒哒哒哒，好想玩原神", "云！原！神！")


def _xhh_vm(num: int) -> int:
    return (255 & ((num << 1) ^ 27)) if num & 128 else num << 1


def _xhh_qm(num: int) -> int:
    return _xhh_vm(num) ^ num


def _xhh_mm(num: int) -> int:
    return _xhh_qm(_xhh_vm(num))


def _xhh_ym(num: int) -> int:
    return _xhh_mm(_xhh_qm(_xhh_vm(num)))


def _xhh_gm(num: int) -> int:
    return _xhh_ym(num) ^ _xhh_mm(num) ^ _xhh_qm(num)


def _xhh_mixed(values: list[int]) -> list[int]:
    return [
        _xhh_gm(values[0]) ^ _xhh_ym(values[1]) ^ _xhh_mm(values[2]) ^ _xhh_qm(values[3]),
        _xhh_qm(values[0]) ^ _xhh_gm(values[1]) ^ _xhh_ym(values[2]) ^ _xhh_mm(values[3]),
        _xhh_mm(values[0]) ^ _xhh_qm(values[1]) ^ _xhh_gm(values[2]) ^ _xhh_ym(values[3]),
        _xhh_ym(values[0]) ^ _xhh_mm(values[1]) ^ _xhh_qm(values[2]) ^ _xhh_gm(values[3]),
        values[4],
        values[5],
    ]


def _xhh_av(value: str, key: str, n: int) -> str:
    pool = key[: len(key) + n]
    return "".join(pool[ord(char) % len(pool)] for char in value)


def _xhh_sv(value: str, key: str) -> str:
    return "".join(key[ord(char) % len(key)] for char in value)


def _xhh_interleave(values: list[str]) -> str:
    output: list[str] = []
    for index in range(len(values[2])):
        for value in values:
            if index < len(value):
                output.append(value[index])
    return "".join(output)


def build_xhh_request_keys(
    path: str,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> tuple[str, str, int]:
    """Build Xiaoheihe's hkey, nonce and request timestamp."""
    request_time = int(timestamp or time.time())
    request_nonce = nonce or hashlib.md5(
        f"{request_time}{secrets.randbelow(max(2, int(time.time() * 1000)))}".encode()
    ).hexdigest().upper()
    values = [
        _xhh_av(str(request_time), _XHH_SIGNING_KEY, -2),
        _xhh_sv(path, _XHH_SIGNING_KEY),
        _xhh_sv(request_nonce, _XHH_SIGNING_KEY),
    ]
    values.sort(key=len)
    digest = hashlib.md5(_xhh_interleave(values).encode()[:20]).hexdigest()
    checksum = sum(_xhh_mixed([ord(char) for char in digest[-6:]])) % 100
    return f"{_xhh_av(digest[:5], _XHH_SIGNING_KEY, -4)}{checksum:02d}", request_nonce, request_time


def build_xhh_token_id(*, timestamp: int | None = None) -> str:
    """Build the short-lived browser token used by Xiaoheihe requests."""
    current = int(timestamp or time.time())
    raw = bytearray(hashlib.md5(str(current).encode()).digest())
    for phrase in _XHH_TOKEN_PHRASES:
        raw.extend(hashlib.md5(phrase.encode()).digest())
    raw.append(0)
    return base64.b64encode(bytes(raw)).decode("ascii")


def build_xhh_request_params(
    path: str,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hkey, nonce, request_time = build_xhh_request_keys(path)
    params: dict[str, Any] = dict(extra or {})
    params.update(
        {
            "os_type": "web",
            "app": "web",
            "client_type": "web",
            "version": "999.0.4",
            "web_version": "2.5",
            "x_client_type": "web",
            "x_app": "heybox_website",
            "x_os_type": "Windows",
            "device_info": "Chrome",
            "hkey": hkey,
            "_time": str(request_time),
            "nonce": nonce,
            "_notip": "true",
        }
    )
    return params


def build_xhh_cookie_header(cookies: dict[str, str]) -> str:
    normalized = {
        str(key).strip(): str(value).strip()
        for key, value in (cookies or {}).items()
        if str(key).strip() and str(value).strip()
    }
    normalized["x_xhh_tokenid"] = build_xhh_token_id()
    return "; ".join(f"{key}={value}" for key, value in normalized.items())

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
