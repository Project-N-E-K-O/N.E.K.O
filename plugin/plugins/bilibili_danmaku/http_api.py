"""
HTTP API 端点（对标 MagicalDanmaku server.cpp getApiContent）

功能：
- /api/netProxy?url=... — 跨域网络代理（GET/POST）
- /api/header?uid=xxx   — 头像获取并缓存
- /api/event             — 模拟事件注入
- /api/status            — 服务状态

架构：
  外部工具/前端 → HTTP API → aiohttp server → B站 API / 插件内部
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore

logger = logging.getLogger(__name__)

# 头像缓存目录
_HEADER_CACHE_DIR = Path(tempfile.gettempdir()) / "neko_bili_headers"


class FakeResolver:
    """固定 IP 的 DNS resolver：将任意 host 解析为同一个已验证 IP
    用于 netProxy 防止 DNS 重绑定：先验证目标 IP，再通过此 resolver 强制连接该 IP"""
    def __init__(self, ip: str):
        self._ip = ip

    async def resolve(self, host, port=0, family=socket.AF_INET):
        return [{"hostname": host, "host": self._ip, "port": port,
                 "family": socket.AF_INET, "proto": socket.IPPROTO_TCP,
                 "flags": socket.AI_NUMERICHOST}]


class HttpApi:
    """
    HTTP API 服务（轻量 aiohttp 服务器）

    对标 C++ server.cpp 的 serverHandleUrl / getApiContent
    """

    def __init__(
        self,
        port: int = 5522,
        host: str = "127.0.0.1",
        event_handler: Optional[Callable] = None,
    ):
        """
        Args:
            port: HTTP 服务端口
            host: 绑定地址
            event_handler: 模拟事件回调 (event_name, data_dict)
        """
        self._port = port
        self._host = host
        self._event_handler = event_handler
        self._app: Any = None
        self._runner: Any = None
        self._site: Any = None
        self._running = False

        # 确保缓存目录存在
        _HEADER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def port(self) -> int:
        return self._port

    @property
    def running(self) -> bool:
        return self._running

    async def start(self):
        """启动 HTTP API 服务"""
        if not aiohttp:
            logger.warning("aiohttp 未安装，HTTP API 服务无法启动")
            return

        from aiohttp import web

        self._app = web.Application()
        self._app.router.add_get("/api/netProxy", self._handle_net_proxy)
        self._app.router.add_post("/api/netProxy", self._handle_net_proxy)
        self._app.router.add_get("/api/header", self._handle_header)
        self._app.router.add_get("/api/event", self._handle_event)
        self._app.router.add_post("/api/event", self._handle_event)
        # CORS preflight for cross-origin POST
        self._app.router.add_route("OPTIONS", "/api/netProxy", self._handle_preflight)
        self._app.router.add_route("OPTIONS", "/api/event", self._handle_preflight)
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/ping", self._handle_ping)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        try:
            self._site = web.TCPSite(self._runner, self._host, self._port)
            await self._site.start()
            self._running = True
            logger.info(f"HTTP API 服务已启动: http://{self._host}:{self._port}")
        except OSError:
            self._port += 1
            self._site = web.TCPSite(self._runner, self._host, self._port)
            await self._site.start()
            self._running = True
            logger.info(f"HTTP API 服务已启动: http://{self._host}:{self._port}")

    async def stop(self):
        """停止 HTTP API 服务"""
        self._running = False
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        logger.info("HTTP API 服务已停止")

    # ── CORS 预检 ─────────────────────────────────────────────────

    @staticmethod
    async def _handle_preflight(request):
        from aiohttp import web
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
                "Access-Control-Max-Age": "86400",
            },
        )

    # ── 路由处理 ─────────────────────────────────────────────────

    async def _handle_net_proxy(self, request):
        """网络代理（解决跨域问题）— 对标 C++ /api/netProxy"""
        url = request.query.get("url", "")
        if not url:
            return self._json_resp({"error": "missing url parameter"}, status=400)

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return self._json_resp({"error": "unsafe URL scheme"}, status=403)

        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        # 单次 DNS 解析 + 验证，避免 DNS 重绑定绕过
        try:
            resolved_ip = await self._resolve_and_validate(host, port, parsed.scheme)
        except ValueError as e:
            return self._json_resp({"error": str(e)}, status=403)

        headers = {}
        # 转发请求头
        for key in request.headers:
            if key.lower() not in ("host", "accept-encoding", "content-length"):
                headers[key] = request.headers[key]

        timeout = aiohttp.ClientTimeout(total=30)
        try:
            # 自定义 DNS resolver：将 host 固定解析为已验证的 IP，保留原 hostname 做 TLS SNI
            resolver = FakeResolver(resolved_ip)
            connector = aiohttp.TCPConnector(
                resolver=resolver,
                force_close=True,
                ttl_dns_cache=0,
            )
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                if request.method == "POST":
                    body = await request.read()
                    async with session.post(url, data=body, headers=headers) as resp:
                        result = await resp.read()
                        content_type = resp.headers.get("Content-Type", "text/plain")
                else:
                    async with session.get(url, headers=headers) as resp:
                        result = await resp.read()
                        content_type = resp.headers.get("Content-Type", "text/plain")

                return self._raw_resp(result, content_type)
        except Exception as e:
            return self._json_resp({"error": str(e)}, status=502)

    async def _resolve_and_validate(self, host: str, port: int, scheme: str) -> str:
        """解析并验证主机地址，返回验证通过的 IP"""
        if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            raise ValueError("blocked localhost address")
        loop = asyncio.get_event_loop()
        try:
            addrs = await loop.getaddrinfo(host, port, family=socket.AF_INET, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            raise ValueError(f"DNS resolution failed for {host}")
        for addr_info in addrs:
            ip = ipaddress.ip_address(addr_info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise ValueError(f"blocked private/loopback address: {ip}")
        return str(addrs[0][4][0])

    async def _handle_header(self, request):
        """头像获取并缓存 — 对标 C++ /api/header"""
        uid = request.query.get("uid", "")
        if not uid:
            return self._json_resp({"error": "missing uid parameter"}, status=400)

        cache_path = _HEADER_CACHE_DIR / f"header_{uid}"

        # 返回缓存
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < 86400:  # 24 小时缓存
                data = cache_path.read_bytes()
                return self._raw_resp(data, "image/jpeg")

        # 获取头像 URL
        try:
            url = f"http://api.bilibili.com/x/space/acc/info?mid={uid}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://space.bilibili.com/",
            }
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    face_url = data.get("data", {}).get("face", "")
                    if not face_url:
                        return self._json_resp({"error": "无法获取头像 URL"}, status=404)

                # 下载头像
                async with session.get(face_url, headers=headers) as img_resp:
                    img_data = await img_resp.read()
                    cache_path.write_bytes(img_data)
                    return self._raw_resp(img_data, "image/jpeg")
        except Exception as e:
            # 返回缓存（即使过期）
            if cache_path.exists():
                data = cache_path.read_bytes()
                return self._raw_resp(data, "image/jpeg")
            return self._json_resp({"error": str(e)}, status=502)

    async def _handle_event(self, request):
        """模拟事件 — 对标 C++ /api/event"""
        event_name = request.query.get("event", "")
        if not event_name:
            return self._json_resp({"cmd": "event", "code": 1, "msg": "缺少 event 参数"})

        try:
            if request.method == "POST":
                data = await request.json()
            else:
                data_str = request.query.get("data", "{}")
                data = json.loads(data_str)
        except json.JSONDecodeError:
            return self._json_resp({"cmd": "event", "code": 2, "msg": "JSON 解析失败"})

        if self._event_handler:
            try:
                await self._event_handler(event_name, data)
            except Exception as e:
                logger.warning(f"模拟事件失败 {event_name}: {e}")

        return self._json_resp({"cmd": "event", "code": 0, "msg": "ok"})

    async def _handle_status(self, _request):
        """返回服务状态"""
        return self._json_resp({
            "status": "running" if self._running else "stopped",
            "port": self._port,
            "host": self._host,
        })

    async def _handle_ping(self, _request):
        """健康检查"""
        return self._raw_resp(b"pong", "text/plain")

    # ── 辅助 ─────────────────────────────────────────────────────

    @staticmethod
    def _json_resp(data: Any, status: int = 200):
        from aiohttp import web
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return web.Response(
            body=body,
            status=status,
            content_type="application/json",
            charset="utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @staticmethod
    def _raw_resp(data: bytes, content_type: str, status: int = 200):
        from aiohttp import web
        extra_headers = {
            "Access-Control-Allow-Origin": "*",
            "Content-Type": content_type,
        }
        return web.Response(body=data, status=status, headers=extra_headers)
