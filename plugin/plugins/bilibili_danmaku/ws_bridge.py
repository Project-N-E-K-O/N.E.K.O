"""
WebSocket 桥接器（对标 MagicalDanmaku server.cpp 架构）

功能：
- 本地 WS Server 监听，接受多个前端连接
- CMD 订阅机制：前端发送 {cmd: "CMDS", data: ["DANMU_MSG", ...]} 选择接收的事件
- CONFIG 双向同步：GET_CONFIG / SET_CONFIG 协议
- 事件广播：将 LiveDanmaku 实时推送到已订阅的前端

架构：
  B站 WS → DanmakuListener → ws_bridge → 本地 WS Server → 多个前端连接
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Set

import websockets

logger = logging.getLogger(__name__)


class WsBridge:
    """
    本地 WebSocket 桥接服务器

    对标 C++ server.cpp 的 danmakuSocketServer:
      openSocketServer() → QWebSocketServer 监听
      processSocketTextMsg() → 处理 CMDS/GET_CONFIG/SET_CONFIG
      sendDanmakuToSockets() → 将事件推送到订阅的 socket
    """

    def __init__(
        self,
        config_provider: Callable[[], dict],
        config_saver: Callable[[dict], None],
        port: int = 5521,
        host: str = "127.0.0.1",
        logger_: logging.Logger | None = None,
    ):
        """
        Args:
            config_provider: 读取当前配置的回调
            config_saver: 保存配置的回调
            port: 本地 WS 服务端口
            host: 绑定地址
        """
        self._config_provider = config_provider
        self._config_saver = config_saver
        self._port = port
        self._host = host

        self._server: Any = None
        self._clients: Set[websockets.WebSocketServerProtocol] = set()
        self._cmd_subscriptions: Dict[int, List[str]] = {}  # id(client) → subscribed CMDs
        self._running = False

    @property
    def port(self) -> int:
        return self._port

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self):
        """启动 WS 桥接服务"""
        if self._running:
            return

        try:
            self._server = await websockets.serve(
                self._handle_connection,
                self._host,
                self._port,
                ping_interval=15,
                ping_timeout=10,
            )
            self._running = True
            logger.info(f"WS 桥接服务已启动: ws://{self._host}:{self._port}")
        except OSError as e:
            logger.warning(f"WS 桥接服务端口 {self._port} 被占用，尝试 {self._port + 1}")
            self._port += 1
            self._server = await websockets.serve(
                self._handle_connection,
                self._host,
                self._port,
                ping_interval=15,
                ping_timeout=10,
            )
            self._running = True
            logger.info(f"WS 桥接服务已启动: ws://{self._host}:{self._port}")

    async def stop(self):
        """停止 WS 桥接服务"""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._clients.clear()
        self._cmd_subscriptions.clear()
        logger.info("WS 桥接服务已停止")

    async def _handle_connection(self, ws: websockets.WebSocketServerProtocol):
        """处理单个前端连接"""
        client_id = id(ws)
        self._clients.add(ws)
        self._cmd_subscriptions[client_id] = []
        logger.info(f"WS 桥接客户端已连接 (当前 {len(self._clients)} 个)")

        try:
            async for message in ws:
                if isinstance(message, str):
                    await self._handle_message(ws, message)
                # 忽略 binary 消息
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            self._cmd_subscriptions.pop(client_id, None)
            logger.info(f"WS 桥接客户端已断开 (剩余 {len(self._clients)} 个)")

    async def _handle_message(self, ws: websockets.WebSocketServerProtocol, message: str):
        """处理前端发来的命令（对标 C++ processSocketTextMsg）"""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"WS 桥接收到无效 JSON: {message}")
            return

        cmd = str(data.get("cmd", "")).upper()
        client_id = id(ws)

        if cmd == "CMDS":
            # 订阅/筛选 CMD
            arr = data.get("data", [])
            if isinstance(arr, list):
                self._cmd_subscriptions[client_id] = [str(x) for x in arr]
                logger.info(f"WS 桥接客户端订阅: {self._cmd_subscriptions[client_id]}")

        elif cmd == "GET_CONFIG":
            # 读取配置
            keys = data.get("data", [])
            group = str(data.get("group", ""))
            current_config = self._config_provider()
            result = {}
            if isinstance(keys, list) and keys:
                for key in keys:
                    result[str(key)] = current_config.get(str(key))
            else:
                result = current_config
            await self._send_json(ws, "GET_CONFIG", result)

        elif cmd == "SET_CONFIG":
            # 保存配置
            config_data = data.get("data", {})
            group = str(data.get("group", ""))
            if isinstance(config_data, dict):
                self._config_saver(config_data)
                logger.info(f"WS 桥接配置已保存: {list(config_data.keys())}")
                await self._send_json(ws, "SET_CONFIG", {"status": "ok"})

        # 其他命令忽略

    async def _send_json(self, ws: websockets.WebSocketServerProtocol, cmd: str, data: Any):
        """向单个客户端发送 JSON"""
        try:
            payload = json.dumps({"cmd": cmd, "data": data}, ensure_ascii=False)
            await ws.send(payload)
        except websockets.exceptions.ConnectionClosed:
            self._clients.discard(ws)
            self._cmd_subscriptions.pop(id(ws), None)

    async def broadcast_event(self, cmd: str, danmaku_data: dict):
        """
        向所有订阅了该事件的前端推送弹幕/事件
        对标 C++ sendDanmakuToSockets(cmd, danmaku)
        """
        if not self._clients:
            return

        payload = json.dumps({"cmd": cmd, "data": danmaku_data}, ensure_ascii=False)
        drop = set()

        for ws in list(self._clients):  # 快照迭代，避免 await 期间 set 被修改
            client_id = id(ws)
            subbed = self._cmd_subscriptions.get(client_id, [])
            # 如果客户端没订阅任何事件 → 推送所有
            # 如果客户端订阅了 → 只推送订阅的事件
            if not subbed or cmd in subbed:
                try:
                    await ws.send(payload)
                except websockets.exceptions.ConnectionClosed:
                    drop.add(ws)

        for ws in drop:
            self._clients.discard(ws)
            self._cmd_subscriptions.pop(id(ws), None)

    async def broadcast_json(self, cmd: str, data: Any):
        """向所有客户端广播 JSON 数据"""
        payload = json.dumps({"cmd": cmd, "data": data}, ensure_ascii=False)
        drop = set()
        for ws in list(self._clients):
            try:
                await ws.send(payload)
            except websockets.exceptions.ConnectionClosed:
                drop.add(ws)
        for ws in drop:
            self._clients.discard(ws)
            self._cmd_subscriptions.pop(id(ws), None)

    async def broadcast_status(self, status: dict):
        """广播连接状态更新"""
        await self.broadcast_json("STATUS", status)
