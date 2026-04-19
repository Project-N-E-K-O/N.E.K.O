# -*- coding: utf-8 -*-
"""
外部 HTTPS 专用的共享 httpx.AsyncClient 单例。

为什么需要：
  每次 `async with httpx.AsyncClient(...)` 构造 SSLContext 初始化开销
  很高（Windows 冷态 ~150ms，事件循环压力下可达 1.1s）。对 web_scraper /
  meme_fetcher / holiday_cache 等跑着跑着就高频访问外网的模块，每次请求
  都重新开 client 既拖慢又把连接池复用的好处全扔了。

覆盖范围：
  **外部安全 HTTPS**（verify=True），允许读 HTTP_PROXY / HTTPS_PROXY 环境
  变量（trust_env=True），默认跟随 30x 跳转（follow_redirects=True）。
  `httpx.AsyncClient` 跨 host 复用安全，连接池按 (scheme, host, port)
  分桶各自 keep-alive。

不适用：
  - 内部 127.0.0.1 服务：用 `utils/internal_http_client.py`
  - 用户一次性大下载（>30MB 或 >30s）：per-call 更直观，避免占用池
  - 需要特殊 SSL 配置 / 自定义 verify 的场景：per-call
  - TTS 长连流式：已有 per-worker 专属 client

并发：
  共享 client **不阻塞并发**。`asyncio.gather(client.get(a), client.get(b))`
  正常，池内自动开多条 TCP（默认 max_connections=100）。

用法：
    from utils.external_http_client import get_external_http_client
    client = get_external_http_client()
    resp = await client.get("https://example.com/api", timeout=10.0)

进程关闭时需调用 `aclose_external_http_client()`。
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None

# 默认超时：多数 scraper / fetcher 调用在 5-10s 区间。调用方可以用
# `client.get(url, timeout=...)` 覆盖单次请求。
_DEFAULT_TIMEOUT = 10.0


def get_external_http_client() -> httpx.AsyncClient:
    """返回进程级共享的外部 HTTPS AsyncClient。首次调用时懒初始化。

    配置：
      - verify=True（默认）：正常校验 TLS 证书
      - trust_env=True：读 HTTP(S)_PROXY / NO_PROXY 环境变量
      - follow_redirects=True：默认跟随 30x 跳转
      - timeout=10.0：请求超时默认值
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            trust_env=True,
            follow_redirects=True,
        )
        logger.debug("[external_http_client] initialized shared AsyncClient")
    return _client


async def aclose_external_http_client() -> None:
    """在 FastAPI shutdown 钩子中调用，释放连接池。"""
    global _client
    if _client is None:
        return
    if not _client.is_closed:
        try:
            await _client.aclose()
            logger.debug("[external_http_client] shared AsyncClient closed")
        except Exception as e:
            logger.debug(f"[external_http_client] close failed: {e}")
    _client = None
