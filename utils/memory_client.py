# -*- coding: utf-8 -*-
"""
Memory Server 专用的共享 httpx.AsyncClient 单例。

为什么需要：
  每次 `async with httpx.AsyncClient(...)` 构造时 httpx 会 eagerly 初始化
  SSLContext（读 certifi / Windows 系统 trust store），即便只请求
  http://127.0.0.1，这个初始化也照跑。冷启动 + 事件循环压力下实测可达
  1.1 秒/次，直接把 `/new_dialog` 的 2 秒 timeout 挤爆，表现为"memory
  server 响应超时"（server 侧其实 ~25ms 就返回了）。

解决方案：
  进程级别复用一个 AsyncClient，显式关闭 SSL 验证（127.0.0.1 纯 http
  不需要），连接池自动复用 TCP 连接。后续每次请求只付实际网络开销。

用法：
    from utils.memory_client import get_memory_client
    client = get_memory_client()
    resp = await client.get(f"http://127.0.0.1:{PORT}/new_dialog/{name}")

进程关闭时需调用 `aclose_memory_client()` 释放连接池。
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None

# 默认超时：与历史三个调用点中最宽松的对齐（5s）。调用方可以用
# `client.get(url, timeout=...)` 针对单次请求覆盖。
_DEFAULT_TIMEOUT = 5.0


def get_memory_client() -> httpx.AsyncClient:
    """返回进程级共享的 AsyncClient。首次调用时懒初始化。

    必须在事件循环内调用（AsyncClient 构造不依赖 loop，但 transport 第一次
    请求时会绑定 loop）。
    """
    global _client
    if _client is None or _client.is_closed:
        # verify=False 彻底跳过 SSLContext 初始化 —— 我们只用来访问
        # 127.0.0.1 的 memory_server，纯 http，不经过 TLS。
        # trust_env=False 不读 HTTP_PROXY/NO_PROXY 等环境变量。
        transport = httpx.AsyncHTTPTransport(verify=False, retries=0)
        _client = httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            proxy=None,
            trust_env=False,
            transport=transport,
        )
        logger.debug("[memory_client] initialized shared AsyncClient (verify=False)")
    return _client


async def aclose_memory_client() -> None:
    """在 FastAPI shutdown 钩子中调用，释放连接池。"""
    global _client
    if _client is None:
        return
    if not _client.is_closed:
        try:
            await _client.aclose()
            logger.debug("[memory_client] shared AsyncClient closed")
        except Exception as e:
            logger.debug(f"[memory_client] close failed: {e}")
    _client = None
