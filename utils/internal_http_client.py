# -*- coding: utf-8 -*-
"""
内部 127.0.0.1 服务专用的共享 httpx.AsyncClient 单例。

为什么需要：
  每次 `async with httpx.AsyncClient(...)` 构造时 httpx 会 eagerly 初始化
  SSLContext（读 certifi / Windows 系统 trust store），即便只请求
  http://127.0.0.1，这个初始化也照跑。冷启动 + 事件循环压力下实测可达
  1.1 秒/次，直接把 `/new_dialog` 的 2 秒 timeout 挤爆，表现为"memory
  server 响应超时"（server 侧其实 ~25ms 就返回了）。

覆盖范围：
  所有 http://127.0.0.1 的内部服务 —— memory_server / agent_server
  (tool_server) / user_plugin_server 等。`httpx.AsyncClient` 本身跨 host
  复用安全，连接池按 (scheme, host, port) 分桶各自 keep-alive，不会互相
  干扰并发。

解决方案：
  进程级别复用一个 AsyncClient，显式关闭 SSL 验证（127.0.0.1 纯 http
  不需要），连接池自动复用 TCP 连接。后续每次请求只付实际网络开销。

用法：
    from utils.internal_http_client import get_internal_http_client
    client = get_internal_http_client()
    resp = await client.get(f"http://127.0.0.1:{PORT}/new_dialog/{name}")

进程关闭时需调用 `aclose_internal_http_client()` 释放连接池。

⚠️ 不得用于外部 HTTPS：`verify=False` 会让中间人随意伪造证书而不报错。
   外部 HTTPS 请用 `utils/external_http_client.py`。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import weakref
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_clients_by_loop: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = weakref.WeakKeyDictionary()
_fallback_client: Optional[httpx.AsyncClient] = None
_clients_lock = threading.RLock()

# 默认超时：与历史三个调用点中最宽松的对齐（5s）。调用方可以用
# `client.get(url, timeout=...)` 针对单次请求覆盖。
_DEFAULT_TIMEOUT = 5.0


def get_internal_http_client() -> httpx.AsyncClient:
    """返回当前事件循环专用的共享 AsyncClient。首次调用时懒初始化。

    `httpx.AsyncClient` 的 transport 在首次请求时会和事件循环绑定。主服务与
    同步连接器线程各自持有独立 loop，因此这里按 loop 隔离，避免跨线程/跨
    loop 复用同一个连接池。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        global _fallback_client
        with _clients_lock:
            if _fallback_client is None or _fallback_client.is_closed:
                _fallback_client = _create_internal_http_client()
                logger.debug("[internal_http_client] initialized fallback AsyncClient (verify=False)")
            return _fallback_client

    with _clients_lock:
        client = _clients_by_loop.get(loop)
        if client is None or client.is_closed:
            client = _create_internal_http_client()
            _clients_by_loop[loop] = client
            logger.debug("[internal_http_client] initialized loop-local AsyncClient (verify=False)")
        return client


def _create_internal_http_client() -> httpx.AsyncClient:
    """创建 127.0.0.1 内部服务专用客户端。"""
    # verify=False 彻底跳过 SSLContext 初始化 —— 我们只用来访问
    # 127.0.0.1 的内部服务，纯 http，不经过 TLS。
    # trust_env=False 不读 HTTP_PROXY/NO_PROXY 等环境变量。
    transport = httpx.AsyncHTTPTransport(verify=False, retries=0)
    return httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT,
        proxy=None,
        trust_env=False,
        transport=transport,
    )


async def _close_client(client: httpx.AsyncClient, *, context: str) -> None:
    if client.is_closed:
        return
    try:
        await client.aclose()
        logger.debug("[internal_http_client] %s AsyncClient closed", context)
    except Exception as e:
        logger.debug("[internal_http_client] close failed (%s): %s", context, e)


async def aclose_internal_http_client_current_loop() -> None:
    """关闭当前事件循环绑定的内部客户端。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    with _clients_lock:
        client = _clients_by_loop.pop(loop, None)
    if client is not None:
        await _close_client(client, context="loop-local")


async def aclose_internal_http_client() -> None:
    """在 FastAPI shutdown 钩子中调用，释放连接池。"""
    global _fallback_client
    with _clients_lock:
        clients = list(_clients_by_loop.items())
        _clients_by_loop.clear()
        fallback_client = _fallback_client
        _fallback_client = None
    for _loop, client in clients:
        await _close_client(client, context="loop-local")

    if fallback_client is not None:
        await _close_client(fallback_client, context="fallback")
