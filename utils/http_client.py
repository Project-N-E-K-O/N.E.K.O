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
"""统一 User-Agent 管理，防止自定义 API 端点的 Cloudflare Bot Management 拦截。

背景：OpenAI / Anthropic 官方 SDK 会自带形如 ``AsyncOpenAI/Python 2.8.1`` 的
User-Agent。Cloudflare 的 "Manage AI bots" 规则会精准识别这些已知 AI SDK 的 UA
并直接 block，导致部分自建 / 反代的自定义 API 端点返回拦截页而非正常响应。

解决办法：给 SDK 客户端注入 ``User-Agent: neko/<版本号>`` 覆盖内置 UA。SDK 的
``default_headers`` 在请求头合并顺序里优先级最高，可以稳定覆盖内置 User-Agent。
"""

from __future__ import annotations
from typing import Any

from config.application import APP_VERSION


def get_default_user_agent() -> str:
    """返回统一 User-Agent，格式 ``neko/<版本号>``（例如 ``neko/0.9.0``）。"""
    return f"neko/{APP_VERSION}"


def ensure_user_agent(headers: dict[str, Any] | None) -> dict[str, Any]:
    """确保 headers 字典里带有默认 User-Agent，返回新字典。

    若调用方已显式提供了 User-Agent（任意大小写），则尊重调用方的值不覆盖；
    否则注入 ``neko/<版本号>``。用于 OpenAI / Anthropic SDK 的 ``default_headers``。

    Args:
        headers: 原始请求头（可为 None）

    Returns:
        合并后的新 headers 字典（至少包含 User-Agent）
    """
    merged: dict[str, Any] = dict(headers) if headers else {}

    # 遍历所有大小写变体，选取首个非空的显式值（跳过 None / 空字符串）
    ua_value: Any | None = None
    for k, value in merged.items():
        if str(k).lower() == "user-agent" and value not in (None, ""):
            ua_value = value
            break

    # 删除所有大小写变体的 user-agent，避免重复请求头
    keys_to_remove = [k for k in merged if str(k).lower() == "user-agent"]
    for k in keys_to_remove:
        del merged[k]

    # 仅写入一个规范的 User-Agent 键
    if ua_value is not None:
        # 调用方显式提供了非空值，保留
        merged["User-Agent"] = ua_value
    else:
        # 调用方未提供或值为空，注入默认值
        merged["User-Agent"] = get_default_user_agent()

    return merged


def patch_requests_default_user_agent() -> None:
    """全局覆盖 requests 库的默认 User-Agent（防止插件用 requests 时被 CF 拦截）。

    仅覆盖 requests 的默认 UA（原本是 ``python-requests/x.y.z``）。显式传入
    ``headers={"User-Agent": ...}`` 的调用方不受影响。启动时调用一次即可。
    """
    import requests.utils

    _default_ua = get_default_user_agent()
    _original_default_user_agent = requests.utils.default_user_agent

    def _neko_default_user_agent(name: str = "python-requests") -> str:
        # name 为默认值时返回 neko UA，否则调用原始函数保留调用方名称
        if name == "python-requests":
            return _default_ua
        return _original_default_user_agent(name)

    requests.utils.default_user_agent = _neko_default_user_agent
