"""
QQ 客户端封装（基于 OneBot 协议）

支持两种 WS 方向（``direction``）：
- **反向**（默认）：启动反向 WebSocket 服务器，等待任意 OneBot v11 实现
  （NapCat / LLOneBot / go-cqhttp / Lagrange 等）作为 WS 客户端连接。
  与 AstrBot 的 aiocqhttp 反向 WS 模式一致。
- **正向**：作为 WS 客户端主动拨出到 OneBot 实现方的 WS 服务器
  （``ws://host:port``），用于远端设备运行 OneBot 实现、插件侧无需暴露端口的
  场景。模式标识为 ``napcat_forward``（``qq_connection_mode`` 的取值，历史命名）。

正向模式复用反向模式的整条入站/出站管线（``_process_incoming`` /
``receive_message`` / echo→future 关联 / 全部 send 包装器），差别只在
transport：把唯一出站 socket 装进 ``_main_client`` / ``_connected_clients``，
由 ``_forward_receive_loop`` 单任务收流并在断线后自动重拨。
"""

import asyncio
import json
import re
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

import websockets
from websockets.exceptions import ConnectionClosed

from .qq_connection import QQConnectionBase


class QQClient(QQConnectionBase):
    #: Observed transport for this connection. Stamped at INGEST, never read
    #: from live config at flush time: a session buffer can span a transport
    #: switch (the switch is immediate and does not clear buffers), so a
    #: flush-time read would attribute old messages to the new transport.
    #: Purely an observed attribute — it is never a key and never affects a
    #: score. See the kill list in ``memory/trust_store.py``.
    CHANNEL: str = "onebot"

    """OneBot 协议客户端（反向 WebSocket 服务器）"""

    def __init__(self, *, onebot_url: str, token: str = "", logger: Any = None,
                 emit_log: Any = None, message_queue_size: int = 100,
                 image_describer: Any = None,
                 voice_transcriber: Any = None,
                 direction: str = "reverse"):
        self._onebot_url = str(onebot_url or "").strip()
        self.token = str(token or "")
        self.logger = logger
        self._emit_log = emit_log or (lambda level, msg: None)
        # 可选：异步回调 (image_url: str) → str，用于对引用消息中的图片做 VLM 描述
        self._image_describer = image_describer
        # 可选：异步回调 (audio_base64: str) → str，用于语音转文字
        self._voice_transcriber = voice_transcriber

        #: WS 方向："reverse"=反向 WS 服务器（默认）/ "forward"=正向 WS 客户端。
        self.direction = str(direction or "reverse").strip().lower()
        #: 连接模式标识，供 runtime 判断是否需要重建连接（reverse→"napcat"，
        #: forward→"napcat_forward"）。见 runtime_ops_service 的 mode 比较。
        self.mode = "napcat_forward" if self.direction == "forward" else "napcat"

        if self.direction == "forward":
            # 正向连接：onebot_url 是 OneBot 实现方 WS 服务器的拨出目标，不拆监听地址
            self._listen_host = "0.0.0.0"
            self._listen_port = 6199
        else:
            # 反向连接：从 onebot_url 解析监听地址
            self._listen_host = "0.0.0.0"
            self._listen_port = 6199
            parsed = urlparse(self._onebot_url) if self._onebot_url else None
            if parsed and parsed.hostname:
                self._listen_host = parsed.hostname
                if parsed.port:
                    self._listen_port = parsed.port

        self._server: Optional[websockets.WebSocketServer] = None
        # 反向模式装的是 server 端协议（多个），正向模式装的是唯一出站客户端
        # 协议——两者都有 .send/.close_code/.close() 且可迭代，代码通用。
        self._connected_clients: set[Any] = set()
        self._main_client: Optional[Any] = None  # 最新的连接，用于发 API 调用
        # 正向模式唯一出站 socket（反向模式为 None）
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._receive_task: Optional[asyncio.Task] = None
        #: 正向模式拨出/重连参数
        self._dial_timeout: float = 10.0
        self._reconnect_initial_backoff: float = 1.0
        self._reconnect_max_backoff: float = 30.0
        self._message_queue_maxsize = max(1, int(message_queue_size or 100))
        self._message_queue: asyncio.Queue = None  # lazy init in connect()
        self._pending_actions: Dict[str, asyncio.Future] = {}
        self._closing = False
        self._sent_message_ids: Dict[str, float] = {}  # message_id → sent_at timestamp
        self._group_muted: Dict[str, float] = {}    # group_id → muted_until (0=not muted, >0=muted until this timestamp)
        self._self_id: str = ""
        self._self_nickname: str = ""

    @property
    def onebot_url(self) -> str:
        return self._onebot_url

    @onebot_url.setter
    def onebot_url(self, value: str) -> None:
        self._onebot_url = str(value or "").strip()
        if self.direction == "forward":
            # 正向模式：onebot_url 是拨出目标，直接使用，不拆监听地址
            return
        self._listen_host = "0.0.0.0"
        self._listen_port = 6199
        parsed = urlparse(self._onebot_url) if self._onebot_url else None
        if parsed and parsed.hostname:
            self._listen_host = parsed.hostname
        if parsed and parsed.port:
            self._listen_port = parsed.port

    def is_connected(self) -> bool:
        # 清理已断开的连接
        dead = {c for c in self._connected_clients if getattr(c, 'close_code', None) is not None}
        self._connected_clients -= dead
        if self._main_client in dead:
            self._main_client = next(iter(self._connected_clients), None)
        return len(self._connected_clients) > 0

    async def get_login_status(self) -> dict[str, Any]:
        if self._connected_clients and self._self_id:
            return {"status": "online", "self_id": self._self_id, "nickname": self._self_nickname or None}
        return {"status": "offline", "self_id": None, "nickname": None}

    def is_group_muted(self, group_id: str) -> bool:
        """检查 bot 是否在该群被禁言（含全体禁言）。"""
        gid = str(group_id or "").strip()
        if not gid:
            return False
        until = self._group_muted.get(gid, 0)
        if until and until > 0:
            import time
            if time.time() < until:
                return True
            # 禁言已过期，清理
            del self._group_muted[gid]
        return False

    def _handle_group_ban_notice(self, notice: dict[str, Any]) -> None:
        """处理禁言通知：记录 bot 被禁言/解除禁言的状态。"""
        import time
        gid = str(notice.get("group_id") or "").strip()
        if not gid:
            return
        sub_type = str(notice.get("sub_type") or "").strip()  # "ban" / "lift_ban"
        user_id = str(notice.get("user_id") or "").strip()
        duration = int(notice.get("duration") or 0)  # 秒, ban时有效

        # 全体禁言 (user_id=0) 或 自己被禁言
        is_whole_group = (user_id == "0")
        is_self = bool(self._self_id and user_id == str(self._self_id))

        if not is_whole_group and not is_self:
            return  # 别人被禁言，不关我们的事

        if sub_type == "ban":
            until = time.time() + max(duration, 1) if duration > 0 else float("inf")
            self._group_muted[gid] = until
            who = "全体" if is_whole_group else "自己"
            self._emit_log("INFO", f"[Mute] {who}被禁言: group={gid} duration={duration}s")
        elif sub_type == "lift_ban":
            self._group_muted.pop(gid, None)
            who = "全体" if is_whole_group else "自己"
            self._emit_log("INFO", f"[Mute] {who}解除禁言: group={gid}")

    @staticmethod
    def _looks_like_path(value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        if text.startswith("file://"):
            return True
        if re.match(r"^[A-Za-z]:[\\/]", text):
            return True
        return text.startswith("/")

    @classmethod
    def _build_image_attachment(cls, segment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(segment, dict) or segment.get("type") != "image":
            return None
        data = segment.get("data")
        if not isinstance(data, dict):
            return None
        raw_url = str(data.get("url") or "").strip()
        raw_path = str(data.get("path") or "").strip()
        raw_file = str(data.get("file") or "").strip()
        locator_type = ""
        locator_value = ""
        if raw_url:
            locator_type = "url"
            locator_value = raw_url
        elif raw_path:
            locator_type = "path"
            locator_value = raw_path
        elif raw_file:
            locator_type = "path" if cls._looks_like_path(raw_file) else "file"
            locator_value = raw_file
        if not locator_value:
            return None
        attachment = {
            "type": "image_url",
            "url": locator_value,
            "locator_type": locator_type,
            "source": "onebot:image",
        }
        if raw_path:
            attachment["path"] = raw_path
        if raw_file:
            attachment["file"] = raw_file
        return attachment

    @classmethod
    def _extract_attachments(cls, raw_msg: Dict[str, Any]) -> list[Dict[str, Any]]:
        segments = raw_msg.get("message")
        if not isinstance(segments, list):
            return []
        attachments: list[Dict[str, Any]] = []
        for segment in segments:
            attachment = cls._build_image_attachment(segment)
            if attachment:
                attachments.append(attachment)
            elif isinstance(segment, dict) and segment.get("type") == "record":
                data = segment.get("data")
                if isinstance(data, dict):
                    file_id = str(data.get("file") or "").strip()
                    if file_id:
                        attachments.append({"type": "record", "file": file_id})
        return attachments

    @classmethod
    def _extract_interaction_context(cls, raw_msg: Dict[str, Any]) -> Dict[str, Any]:
        segments = raw_msg.get("message")
        self_id = str(raw_msg.get("self_id") or "").strip()
        if not isinstance(segments, list):
            return {
                "quoted_message_id": "",
                "quoted_sender_id": "",
                "mentioned_user_ids": [],
                "mentions_other_user": False,
                "mentions_all": False,
                "mentions_bot": False,
            }

        quoted_message_id = ""
        quoted_sender_id = ""
        mentioned_user_ids: list[str] = []
        mentions_other_user = False
        mentions_all = False
        mentions_bot = False

        for segment in segments:
            if not isinstance(segment, dict):
                continue
            segment_type = str(segment.get("type") or "").strip()
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            if segment_type == "reply":
                quoted_message_id = str(data.get("id") or data.get("message_id") or quoted_message_id).strip()
                quoted_sender_id = str(data.get("user_id") or data.get("qq") or "").strip()
                continue
            if segment_type != "at":
                continue
            mentioned_id = str(data.get("qq") or "").strip()
            if not mentioned_id:
                continue
            if mentioned_id == "all":
                mentions_all = True
                continue
            mentioned_user_ids.append(mentioned_id)
            if self_id and mentioned_id == self_id:
                mentions_bot = True
            else:
                mentions_other_user = True

        return {
            "quoted_message_id": quoted_message_id,
            "quoted_sender_id": quoted_sender_id,
            "mentioned_user_ids": mentioned_user_ids,
            "mentions_other_user": mentions_other_user,
            "mentions_all": mentions_all,
            "mentions_bot": mentions_bot,
        }

    # ── 连接生命周期 ─────────────────────────────────────────

    async def connect(self):
        """建立连接。

        反向模式：启动反向 WebSocket 服务器，等待 OneBot 客户端拨入；
        正向模式：启动后台收流/重连循环（由循环负责拨出），返回即幂等。
        正向**不**阻塞拨出：OneBot 实现方的 WS 服务器要在其本地进程起来并
        登录后才开始监听，start 时可能还没就绪——由 ``_forward_receive_loop``
        按退避重试，与反向模式「等待 OneBot 拨入」的语义一致。
        """
        self._closing = False
        if self.direction == "forward":
            if self._receive_task is not None and not self._receive_task.done():
                return  # 收流/重连循环已在跑，幂等
            self._message_queue = asyncio.Queue(maxsize=self._message_queue_maxsize)
            self._receive_task = asyncio.create_task(self._forward_receive_loop())
            return
        if self._server is not None:
            return
        # 在当前 event loop 中重新创建队列（避免跨 loop 绑定错误）
        self._message_queue = asyncio.Queue(maxsize=self._message_queue_maxsize)
        self._server = await websockets.serve(
            self._handle_client,
            host=self._listen_host,
            port=self._listen_port,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        )
        if self.logger:
            self.logger.info(f"Reverse WS server listening on {self._listen_host}:{self._listen_port}")

    async def disconnect(self):
        """关闭连接，清理资源"""
        self._closing = True

        # 取消所有待处理请求
        for future in list(self._pending_actions.values()):
            if not future.done():
                future.cancel()
        self._pending_actions.clear()

        if self.direction == "forward":
            if self._receive_task:
                self._receive_task.cancel()
                try:
                    await self._receive_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._receive_task = None
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
            self._connected_clients.clear()
            self._main_client = None
            if self.logger:
                self.logger.info("Forward WS client stopped")
            return

        # 关闭所有已连接的客户端
        for client in list(self._connected_clients):
            try:
                await client.close()
            except Exception:
                pass
        self._connected_clients.clear()
        self._main_client = None

        # 停止服务器
        if self._server:
            try:
                self._server.close()
            except RuntimeError:
                pass  # event loop already closed
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

        if self.logger:
            self.logger.info("Reverse WS server stopped")

    # ── 正向 WebSocket 客户端 ─────────────────────────────────

    def _forward_ws_url(self) -> str:
        """构造正向拨出 URL：token 走 Authorization 头之外，也拼到 query
        （?access_token=，幂等：已带则不重复），兼容只认 query 的 OneBot 实现。"""
        url = self._onebot_url
        if not self.token:
            return url
        parsed = urlparse(url)
        if "access_token" in parse_qs(parsed.query):
            return url
        sep = "&" if parsed.query else "?"
        return f"{url}{sep}{urlencode({'access_token': self.token})}"

    def _redact_url(self, url: str) -> str:
        """写日志前遮掉 URL query 里的 access_token，避免 token 明文落盘。

        ``_forward_ws_url`` 会把 token 拼进 query；连接成功后若把完整 URL 写进
        文件日志，token 就永久留在磁盘上了。日志里只保留主机/路径，token 值以
        ``***`` 替代（与插件 ``_mask_token`` 的脱敏习惯一致）。
        """
        if not url:
            return url
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if "access_token" not in params:
                return url
            cleaned = "&".join(
                f"{k}={'***' if k == 'access_token' else v}"
                for k, values in params.items()
                for v in values
            )
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, cleaned, parsed.fragment))
        except Exception:
            return url

    def _redact_text(self, text: str) -> str:
        """把文本里出现的明文 token 遮掉（异常消息可能内嵌带 token 的完整 URL）。

        同时替换原始值与 URL 编码值：token 若含 ``+``/``/``/``=`` 等字符，URL 里
        是编码形式（%2B / %2F / %3D），只替换原始值会漏。
        """
        try:
            raw = str(text)
            if self.token:
                for variant in (self.token, quote(self.token, safe="")):
                    if variant and variant in raw:
                        raw = raw.replace(variant, "***")
            return raw
        except Exception:
            return str(text)

    async def _dial_forward(self) -> bool:
        """正向拨出到 OneBot 实现方的 WS 服务器（收流循环调用）。

        失败不 raise，记录日志并返回 False，由 ``_forward_receive_loop`` 按
        退避重试——WS 服务器可能尚未就绪（进程还在启动/登录）。
        """
        url = self._forward_ws_url()
        try:
            ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    additional_headers=(
                        {"Authorization": f"Bearer {self.token}"} if self.token else None
                    ),
                    ping_interval=30,
                    ping_timeout=10,
                    max_size=2 ** 23,
                ),
                timeout=self._dial_timeout,
            )
        except Exception as e:
            # 异常消息可能内嵌带 token 的完整 URL（InvalidURI/InvalidStatus 等），先遮掉
            self._emit_log("WARN", f"OneBot(正向) 拨出失败: {self._redact_text(e)}")
            return False
        self._ws = ws
        self._main_client = ws
        self._connected_clients = {ws}
        if self.logger:
            # 不打印带 access_token 的完整 URL，token 会明文留在日志文件里
            self.logger.info(f"Forward WS connected to {self._redact_url(url)}")
        self._emit_log("INFO", "OneBot(正向) 已连接")
        # 首次连接时异步获取登录信息（不阻塞收流循环）
        if not self._self_id:
            asyncio.create_task(self._fetch_login_info_async())
        return True

    async def _forward_receive_loop(self):
        """正向模式收流 + 断线重连循环。

        单任务同时负责 recv 与重拨：断线后按退避重拨、换新 socket 继续收，
        等价于反向模式下 OneBot 实现自己重连（只是发起方在我们这侧）。
        """
        backoff = self._reconnect_initial_backoff
        while not self._closing:
            if self._ws is None or getattr(self._ws, "close_code", None) is not None:
                ok = await self._dial_forward()
                if not ok:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self._reconnect_max_backoff)
                    continue
                backoff = self._reconnect_initial_backoff
            ws = self._ws
            try:
                async for raw_message in ws:
                    try:
                        await self._process_incoming(raw_message)
                    except Exception:
                        if self.logger:
                            self.logger.exception("Error processing incoming message")
            except ConnectionClosed:
                pass
            except asyncio.CancelledError:
                break
            except Exception:
                if self.logger and not self._closing:
                    self.logger.exception("Unexpected error in forward receive loop")
            if self._closing:
                break
            self._emit_log("WARN", "OneBot(正向) 连接断开，等待重连...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._reconnect_max_backoff)

    # ── 客户端连接处理 ────────────────────────────────────────

    def _check_token(self, websocket: websockets.WebSocketServerProtocol) -> bool:
        """验证客户端 token，如果未配置 token 则允许所有连接"""
        if not self.token:
            return True

        # 从 query string 提取 access_token
        try:
            request_path = websocket.request.path if websocket.request else "/"
        except Exception:
            request_path = "/"
        parsed = urlparse(request_path)
        params = parse_qs(parsed.query)
        access_token = params.get("access_token", [None])[0]
        if access_token == self.token:
            return True

        # 从 Authorization header 提取 Bearer token
        try:
            auth_header = websocket.request.headers.get("Authorization", "") if websocket.request else ""
        except Exception:
            auth_header = ""
        if auth_header.startswith("Bearer ") and auth_header[7:] == self.token:
            return True

        return False

    async def _handle_client(self, websocket: websockets.WebSocketServerProtocol):
        """处理一个 OneBot 客户端连接"""
        # Token 鉴权
        if not self._check_token(websocket):
            if self.logger:
                addr = websocket.remote_address if hasattr(websocket, 'remote_address') else "unknown"
                self.logger.warning(f"Rejected unauthorized client from {addr}")
            await websocket.close(1008, "Unauthorized")
            return

        # 注册客户端
        self._connected_clients.add(websocket)
        was_first = self._main_client is None
        self._main_client = websocket
        addr = websocket.remote_address if hasattr(websocket, 'remote_address') else "unknown"
        if self.logger:
            self.logger.info(f"OneBot client connected from {addr}")
        if was_first:
            self._emit_log("INFO", "OneBot 已连接")
        else:
            self._emit_log("INFO", f"OneBot 重连成功(共{len(self._connected_clients)}个客户端)")

        # 首次连接时异步获取登录信息（不阻塞消息循环）
        if not self._self_id:
            import asyncio
            asyncio.create_task(self._fetch_login_info_async())

        try:
            async for raw_message in websocket:
                try:
                    await self._process_incoming(raw_message)
                except Exception:
                    if self.logger:
                        self.logger.exception("Error processing incoming message")
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.logger and not self._closing:
                self.logger.exception("Unexpected error in client handler")
        finally:
            self._connected_clients.discard(websocket)
            was_main = self._main_client is websocket
            if was_main:
                self._main_client = next(iter(self._connected_clients), None)
            addr = websocket.remote_address if hasattr(websocket, 'remote_address') else "unknown"
            if self.logger:
                self.logger.info(f"OneBot client disconnected from {addr}")
            if was_main:
                remaining = len(self._connected_clients)
                if remaining > 0:
                    self._emit_log("WARN", f"OneBot 主连接断开(剩余{remaining})，已切换备用")
                else:
                    self._emit_log("ERROR", "OneBot 已断开，等待重连...")

    # ── 消息处理 ──────────────────────────────────────────────

    def _transcribe_record_segments(self, message: Dict[str, Any]) -> list[str]:
        """提取语音段信息，返回需要异步获取的 file_id 列表。支持数组、CQ码字符串、raw_message。"""
        import re
        segments = message.get("message")
        record_files: list[str] = []
        # 格式1: OneBot 数组段
        if isinstance(segments, list):
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                if seg.get("type") == "record":
                    f = str(seg.get("data", {}).get("file") or "").strip()
                    if f:
                        record_files.append(f)
        # 格式2: CQ 码字符串 (message 字段)
        if isinstance(segments, str):
            for m in re.finditer(r'\[CQ:record,\s*file=([^,\]]+)', segments):
                f = m.group(1).strip()
                if f:
                    record_files.append(f)
        # 格式3: raw_message 字符串 (部分 OneBot 私聊实现)
        raw_msg = message.get("raw_message")
        if isinstance(raw_msg, str) and not record_files:
            for m in re.finditer(r'\[CQ:record,\s*file=([^,\]]+)', raw_msg):
                f = m.group(1).strip()
                if f:
                    record_files.append(f)
        if record_files:
            self._emit_log("DEBUG", f"[Voice] 检测到 {len(record_files)} 条语音 (file_id={record_files})")
        else:
            self._emit_log("DEBUG", f"[Voice] 未检测到语音段, msg_type={message.get('message_type')} segments_type={type(segments).__name__} segments={str(segments)[:100]}")
        return record_files

    def _collect_file_segments(self, message: Dict[str, Any]) -> list[dict]:
        """提取文件段信息，返回 ``[{file_id, name, url, busid}]``。

        支持 OneBot 数组段与 CQ 码字符串(raw_message)两种形态；NapCat 的
        ``file_id`` 缺省时退化为文件名兜底，供后台 ``_fetch_file_content`` 拉内容。
        """
        import re
        segments = message.get("message")
        files: list[dict] = []
        if isinstance(segments, list):
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                if seg.get("type") != "file":
                    continue
                # 外部 OneBot 客户端不可信：data 可能不是 dict、busid 可能不是
                # 整数——边界上容错，任何段解析失败都不能拖垮整条消息。
                d = seg.get("data")
                if not isinstance(d, dict):
                    d = {}
                try:
                    busid = int(d.get("busid") or 0)
                except (TypeError, ValueError):
                    busid = 0
                files.append({
                    "file_id": str(d.get("file_id") or "").strip(),
                    "name": str(
                        d.get("file_name") or d.get("name") or d.get("file") or ""
                    ).strip(),
                    "url": str(d.get("url") or "").strip(),
                    "busid": busid,
                })
        if not files:
            raw = str(message.get("raw_message") or message.get("message") or "")
            for m in re.finditer(r"\[CQ:file,[^\]]*\]", raw):
                text = m.group(0)
                fid = re.search(r"file_id=([^,\]]+)", text)
                fname = re.search(r"file=([^,\]]+)", text)
                busid = re.search(r"busid=(\d+)", text)
                files.append({
                    "file_id": fid.group(1).strip() if fid else "",
                    "name": fname.group(1).strip() if fname else "",
                    "url": "",
                    # 保留 CQ 段里的 busid：get_group_file_url 需要它才能解析
                    # 部分 OneBot 后端的文件 URL（与数组段分支行为一致）。
                    "busid": int(busid.group(1)) if busid else 0,
                })
        return files

    @staticmethod
    def _expand_forward_segments(message: Dict[str, Any]) -> list[str]:
        """展开消息中的转发段（forward/合并转发），提取嵌套消息文本。

        将转发消息中的每条子消息格式化为 "[转发] 发送者: 内容" 追加到 raw_message。
        返回未能从内联数据解析、需要通过 API 获取的 forward_id 列表。
        """
        segments = message.get("message")
        if not isinstance(segments, list):
            return []
        forward_texts: list[str] = []
        unresolved_ids: list[str] = []
        for seg in segments:
            if not isinstance(seg, dict) or seg.get("type") != "forward":
                continue
            data = seg.get("data") or {}
            forward_id = str(data.get("id") or "").strip()
            # 某些 OneBot 实现直接展开子消息在 data.messages 里
            sub_msgs = data.get("messages") or []
            if isinstance(sub_msgs, list) and sub_msgs:
                for sub in sub_msgs:
                    if not isinstance(sub, dict):
                        continue
                    sender = sub.get("sender", {})
                    sender_name = (sender.get("card") or sender.get("nickname") or str(sub.get("user_id") or "")).strip()
                    sub_segments = sub.get("message") or []
                    sub_text = ""
                    if isinstance(sub_segments, list):
                        for s in sub_segments:
                            if isinstance(s, dict) and s.get("type") == "text":
                                sub_text += str(s.get("data", {}).get("text", ""))
                            elif isinstance(s, dict) and s.get("type") == "image":
                                sub_text += "[图片]"
                            elif isinstance(s, dict) and s.get("type") == "face":
                                sub_text += "[表情]"
                    elif isinstance(sub_segments, str):
                        sub_text = sub_segments
                    if sub_text.strip():
                        forward_texts.append(f"[转发] {sender_name}: {sub_text.strip()}")
            elif forward_id:
                unresolved_ids.append(forward_id)
        if forward_texts:
            raw = str(message.get("raw_message") or "").strip()
            expanded = "\n".join(forward_texts)
            message["raw_message"] = f"{raw}\n{expanded}" if raw else expanded
            if not message.get("content"):
                message["content"] = message["raw_message"]
            # 转发子条数计入缓冲计数
            message["_forward_sub_count"] = len(forward_texts)
        return unresolved_ids

    @staticmethod
    def _expand_reply_segments(message: Dict[str, Any]) -> list[str]:
        """提取引用回复消息的 ID 列表，供异步获取完整内容（兼容 KiraAI 方案）。"""
        segments = message.get("message")
        if not isinstance(segments, list):
            return []
        reply_ids: list[str] = []
        for seg in segments:
            if isinstance(seg, dict) and seg.get("type") == "reply":
                rid = str((seg.get("data") or {}).get("id") or "").strip()
                if rid:
                    reply_ids.append(rid)
        return reply_ids

    async def _fetch_forward_content(self, message: Dict[str, Any], unresolved_ids: list[str]) -> None:
        """通过 API 获取转发消息内容，构建 MessageChain 并追加到 raw_message。

        每条子消息带 `[时间] 发送者: 内容` 格式（对齐 KiraAI）。"""
        from datetime import datetime as _dt
        seen: set[str] = set()
        forward_texts: list[str] = []
        for fid in unresolved_ids:
            if fid in seen:
                continue
            seen.add(fid)
            try:
                data = await self.get_forward_msg(fid)
                chains = await self._build_forward_chains(data)
                for chain in chains:
                    sender = chain.sender_name or chain.sender_id or "未知用户"
                    ts_str = ""
                    if chain.timestamp:
                        ts_str = _dt.fromtimestamp(chain.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                    prefix = f"[转发] [{ts_str}] {sender}: " if ts_str else f"[转发] {sender}: "
                    forward_texts.append(prefix + chain.repr)
            except Exception:
                if self.logger:
                    self.logger.exception(f"Failed to fetch forward msg {fid}")
        if forward_texts:
            raw = str(message.get("raw_message") or "").strip()
            expanded = "\n".join(forward_texts)
            message["raw_message"] = f"{raw}\n{expanded}" if raw else expanded
            content = str(message.get("content") or "").strip()
            message["content"] = f"{content}\n{expanded}" if content else message["raw_message"]
            prev = message.get("_forward_sub_count", 0)
            message["_forward_sub_count"] = prev + len(forward_texts)

    _MAX_REPLY_DEPTH = 3  # 递归展开引用链的最大深度

    async def _build_message_chain(self, msg_data: dict[str, Any], *, depth: int = 0, seen: set[str] | None = None) -> "MessageChain":
        """将 OneBot 消息 dict 转为 MessageChain，递归展开引用/转发。"""
        from .message_chain import (
            MessageChain, Text, Image, At, Reply, Forward, Emoji, Record, JsonCard, File,
        )
        if seen is None:
            seen = set()
        msg = msg_data.get("data") or msg_data
        chain = MessageChain(
            sender_name=self._resolve_reply_sender(msg),
            sender_id=str(msg.get("user_id") or ""),
            timestamp=int(msg.get("time") or 0),
            message_id=str(msg.get("message_id") or ""),
        )
        segments = msg.get("message") or []
        if not isinstance(segments, list):
            raw = str(msg.get("raw_message") or "")
            import re as _re
            chain.add(Text(_re.sub(r"\[CQ:[^]]+]", "", raw).strip()))
            return chain
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            data = seg.get("data") or {}
            st = seg.get("type") or ""
            if st == "text":
                chain.add(Text(str(data.get("text") or "")))
            elif st == "image":
                img_url = str(data.get("url") or data.get("file") or "")
                desc = ""
                if img_url and self._image_describer and depth <= self._MAX_REPLY_DEPTH:
                    try:
                        desc = await asyncio.wait_for(self._image_describer(img_url), timeout=8.0)
                    except Exception:
                        pass
                if desc:
                    chain.add(Text(f"[Image {desc}]"))
                else:
                    chain.add(Image(url=img_url))
            elif st == "face":
                chain.add(Emoji(emoji_id=str(data.get("id") or "")))
            elif st == "at":
                qq = str(data.get("qq") or "")
                if not qq:
                    continue
                nickname = ""
                if qq != "all":
                    # 群消息优先查群成员 card，card 为空或私聊再 get_stranger_info；
                    # 失败返回 ""（At.repr 回落 [@用户{qq}]），消息不崩。
                    nickname = await self._resolve_at_nickname(qq, msg)
                chain.add(At(pid=qq, nickname=nickname))
            elif st == "record":
                chain.add(Record(file_id=str(data.get("file") or "")))
            elif st == "video":
                chain.add(Text("[视频]"))
            elif st == "json":
                chain.add(JsonCard(raw_json=str(data.get("data") or "")))
            elif st == "file":
                chain.add(await self._build_file_element(data, msg))
            elif st == "reply":
                rid = str(data.get("id") or "").strip()
                inner_chain = MessageChain.empty()
                if rid and depth < self._MAX_REPLY_DEPTH and rid not in seen:
                    seen.add(rid)
                    try:
                        inner_data = await self.get_msg(rid)
                        inner_chain = await self._build_message_chain(inner_data, depth=depth + 1, seen=seen)
                    except Exception:
                        pass
                chain.add(Reply(message_id=rid, chain=inner_chain))
            elif st == "forward":
                fid = str(data.get("id") or "").strip()
                sub_chains: list[MessageChain] = []
                if fid and depth < self._MAX_REPLY_DEPTH and fid not in seen:
                    seen.add(fid)
                    try:
                        forward_data = await self.get_forward_msg(fid)
                        sub_chains = await self._build_forward_chains(forward_data, depth=depth + 1, seen=seen)
                    except Exception:
                        pass
                chain.add(Forward(chains=sub_chains))
        return chain

    async def _build_file_element(self, data: dict[str, Any], msg: dict[str, Any]) -> "File":
        """把 OneBot ``file`` 段转成 File 组件，兼容各后端获取真实 URL 的方式。

        - Lagrange 类后端：``data.url`` 直接带 http 直链 → 直接用；
        - NapCat / go-cqhttp 类：只有 ``file_id`` → 调 ``get_group_file_url`` /
          ``get_private_file_url`` 换 URL；
        - 其余/失败：完全回落现状 ``data.file`` 兜底。

        任何 API 异常都被吞掉，绝不向外抛 —— 单个 file 段失败不影响整条链。
        """
        from .message_chain import File
        raw_file = str(data.get("file") or "")
        url = str(data.get("url") or "").strip()
        # 1) Lagrange / 带直链后端：data.url 以 http 开头 → 直接取
        if url.startswith(("http://", "https://")):
            name = (
                str(data.get("file_name") or "")
                or str(data.get("name") or "")
                or raw_file
                or "file"
            )
            return File(name=name, url=url)
        # 2) NapCat 路线：有 file_id 且有群/私聊上下文 → 调 API 换 URL
        file_id = str(data.get("file_id") or "").strip()
        if file_id:
            msg_type = str(msg.get("message_type") or "").strip()
            group_id = str(msg.get("group_id") or "").strip()
            try:
                if msg_type == "group" and group_id:
                    # 群文件：有真实 busid 才走 get_group_file_url（NapCat 要求真
                    # busid，0 会被拒）；只有 file_id 时用通用 get_file 兜底。
                    try:
                        busid = int(data.get("busid") or 0)
                    except (TypeError, ValueError):
                        busid = 0
                    if busid:
                        ret = await self.get_group_file_url(
                            group_id, file_id, busid=busid,
                        )
                    else:
                        ret = await self.get_file_by_id(file_id)
                elif msg_type == "private":
                    # NapCat 的 get_private_file_url 需要发送者 user_id，缺了就
                    # 拿不到 URL，落到下方兜底（裸 [文件 name]）。
                    sender_id = str(msg.get("user_id") or "").strip()
                    ret = (
                        await self.get_private_file_url(sender_id, file_id)
                        if sender_id
                        else None
                    )
                else:
                    ret = None
                ret_url = str((ret or {}).get("url") or "").strip()
                if ret_url:
                    name = (
                        str(ret.get("file_name") or "")
                        or str(ret.get("name") or "")
                        or raw_file
                        or "file"
                    )
                    return File(name=name, url=ret_url)
            except Exception:
                pass  # ActionFailed / 超时 / 未连接 → 降级
        # 3) 兜底：保持现状 data.file 行为
        return File(name=raw_file)

    async def _resolve_at_nickname(self, qq: str, msg: dict[str, Any]) -> str:
        """解析 @ 目标的昵称（供 At.repr 注入 prompt）。

        群消息：优先 ``get_group_member_info`` 的 card，card 为空再
        ``get_stranger_info``；私聊（无 group_id）直接 stranger。
        任何 API 异常都被吞掉，返回 ""（At.repr 回落 ``[@用户{qq}]``）。
        """
        group_id = str(msg.get("group_id") or "").strip()
        if group_id:
            try:
                info = await self.get_group_member_info(group_id, qq, no_cache=False)
                nickname = str((info or {}).get("card") or "").strip()
                if nickname:
                    return nickname
            except Exception:
                pass  # 降级到 stranger
        try:
            info = await self.get_stranger_info(qq, no_cache=False)
            return str(
                (info or {}).get("nick") or (info or {}).get("nickname") or ""
            ).strip()
        except Exception:
            return ""

    async def _build_forward_chains(self, forward_data: dict[str, Any], *, depth: int = 0, seen: set[str] | None = None) -> "list[MessageChain]":
        """从 get_forward_msg 返回构建 MessageChain 列表，每条子消息带时间戳。"""
        from .message_chain import MessageChain, Text
        messages = forward_data.get("messages") or forward_data.get("data", {}).get("messages") or []
        if not isinstance(messages, list):
            return []
        chains: list[MessageChain] = []
        for sub in messages:
            if not isinstance(sub, dict):
                continue
            chain = await self._build_message_chain(sub, depth=depth, seen=seen)
            chains.append(chain)
        return chains

    async def _fetch_reply_content(self, message: Dict[str, Any], reply_ids: list[str]) -> None:
        """递归展开引用链：通过 get_msg API 构建嵌套 MessageChain。"""
        from .message_chain import MessageChain
        seen: set[str] = set()
        chains: list[MessageChain] = []
        first_sender_id = ""

        for rid in reply_ids:
            if rid in seen:
                continue
            seen.add(rid)
            try:
                data = await self.get_msg(rid)
                chain = await self._build_message_chain(data, depth=0, seen=seen)
                if chain.elements:
                    chains.append(chain)
                    if not first_sender_id and chain.sender_id:
                        first_sender_id = chain.sender_id
            except Exception:
                if self.logger:
                    self.logger.exception(f"Failed to fetch reply msg {rid}")

        if first_sender_id:
            message["_cached_reply_sender_id"] = first_sender_id
        if chains:
            # 用 MessageChain.repr 生成可读引用上下文
            lines: list[str] = []
            for chain in chains:
                sender = chain.sender_name or chain.sender_id or "未知用户"
                ts = chain.timestamp
                time_str = ""
                if ts:
                    from datetime import datetime as _dt
                    time_str = _dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                header = f"[↑ {sender}"
                if time_str:
                    header += f" {time_str}"
                header += f": {chain.repr}]"
                lines.append(header)
            import re as _re
            raw = str(message.get("raw_message") or "").strip()
            raw = _re.sub(r"\[CQ:reply,\s*id=\d+[^\]]*\]", "", raw).strip()
            message["raw_message"] = raw if raw else str(message.get("raw_message") or "")
            if not message.get("content"):
                message["content"] = message["raw_message"]
            message["_reply_context"] = "\n".join(lines)
            message["_reply_chains"] = chains

    @staticmethod
    def _resolve_reply_sender(msg_data: dict[str, Any]) -> str:
        sender = msg_data.get("sender", {}) or {}
        name = sender.get("card") or sender.get("nickname") or ""
        if str(name).strip():
            return str(name).strip()
        uid = str(msg_data.get("user_id") or "").strip()
        return f"QQ用户{uid}" if uid else "未知用户"

    async def _fetch_record_content(self, message: Dict[str, Any], record_files: list[str]) -> None:
        """异步获取语音文件并转文字注入 raw_message。有 transcriber 则转文字，否则标记 [语音]。"""
        import base64 as _b64
        for file_id in record_files:
            try:
                self._emit_log("DEBUG", f"[Voice] 获取语音: file={file_id} has_transcriber={self._voice_transcriber is not None}")
                data = await self.get_record(file_id)
                # get_record 返回 URL 或本地路径，需获取文件内容后 base64 编码
                url = str((data.get("data") or {}).get("url") or data.get("url") or "").strip()
                file_path = str((data.get("data") or {}).get("file") or data.get("file") or "").strip()
                record_bytes = b""
                if url:
                    import httpx
                    try:
                        async with httpx.AsyncClient(timeout=30.0, proxy=None, trust_env=False) as cl:
                            resp = await cl.get(url)
                            if resp.status_code == 200:
                                record_bytes = resp.content
                    except Exception:
                        pass
                if not record_bytes and file_path:
                    from pathlib import Path as _Path
                    _fp = _Path(file_path)
                    if _fp.is_file():
                        record_bytes = await asyncio.to_thread(_fp.read_bytes)
                record_b64 = _b64.b64encode(record_bytes).decode() if record_bytes else ""
                self._emit_log("DEBUG", f"[Voice] get_record完成: b64_len={len(record_b64)} url_len={len(url)}")
                # 优先传 URL（Qwen DashScope 只接受 URL），回退 base64
                transcript = ""
                if self._voice_transcriber:
                    try:
                        if url:
                            transcript = await self._voice_transcriber(audio_url=url)
                        if not transcript and record_b64:
                            transcript = await self._voice_transcriber(record_b64)
                    except Exception:
                        if self.logger:
                            self.logger.exception("语音转文字失败")
                if transcript:
                    raw = str(message.get("raw_message") or "").strip()
                    message["raw_message"] = f"[语音] {transcript} {raw}".strip()
                    message["content"] = message["raw_message"]
                    self._emit_log("INFO", f"[Voice] 语音转文字完成: {transcript[:40]}")
                    continue
                # 回退：保留已有文本，仅标记 [语音]
                existing = str(message.get("content") or message.get("raw_message") or "").strip()
                marker = "[语音]"
                if marker not in existing:
                    message["content"] = f"{marker} {existing}".strip() if existing else marker
            except Exception:
                if self.logger:
                    self.logger.exception(f"Failed to fetch record {file_id}")

    #: 文本文件内容注入上限（超出截断并标注，防撑爆上下文）
    _FILE_TEXT_MAX_BYTES = 100 * 1024
    #: 视为图片、走 VLM 链路的文件扩展名
    _IMAGE_FILE_EXTENSIONS = frozenset(
        {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".heic", ".svg"}
    )

    async def _fetch_file_content(self, message: Dict[str, Any], files: list[dict]) -> None:
        """后台拉取文件内容：图片走 VLM，文本解码，二进制/失败标记；替换 CQ 码。

        分类路由：
        - 图片扩展名 → 走现有 ``_image_describer(url)``（VLM 链路，吃 URL）；
        - 其他 → 下载字节，前 512 字节含 NUL 判二进制，否则按 utf-8 解码
          （上限 ``_FILE_TEXT_MAX_BYTES``，超出截断并标注）；
        - 失败 → 裸 ``[文件 {name}]`` 兜底。

        最后把 ``content``/``raw_message`` 里的原始 ``[CQ:file,...]`` 替换成可读文本，
        让 LLM 本回合直接看到文件内容（与语音转录一致）。
        """
        import re
        from pathlib import Path as _Path
        import httpx

        renders: list[str] = []
        msg_type = str(message.get("message_type") or "").strip()
        group_id = str(message.get("group_id") or "").strip()

        for f in files:
            file_id = str(f.get("file_id") or "").strip()
            name = str(f.get("name") or "").strip() or file_id or "文件"
            url = str(f.get("url") or "").strip()
            render: str | None = None
            try:
                if not url and file_id:
                    if msg_type == "group" and group_id:
                        # 有真实 busid 才走 get_group_file_url，否则通用 get_file
                        # 兜底（群文件消息常只带 file_id）。
                        try:
                            busid = int(f.get("busid") or 0)
                        except (TypeError, ValueError):
                            busid = 0
                        if busid:
                            ret = await self.get_group_file_url(
                                group_id, file_id, busid=busid,
                            )
                        else:
                            ret = await self.get_file_by_id(file_id)
                        url = str((ret or {}).get("url") or "").strip()
                    elif msg_type == "private":
                        # NapCat 的 get_private_file_url 需要发送者 user_id，缺了
                        # 就拿不到 URL，落到下方兜底。
                        sender_id = str(message.get("user_id") or "").strip()
                        if sender_id:
                            ret = await self.get_private_file_url(sender_id, file_id)
                            url = str((ret or {}).get("url") or "").strip()
                if not url:
                    render = f"[文件 {name}]"
                elif _Path(name).suffix.lower() in self._IMAGE_FILE_EXTENSIONS:
                    desc = ""
                    if self._image_describer:
                        try:
                            desc = await asyncio.wait_for(
                                self._image_describer(url), timeout=15.0,
                            )
                        except Exception:
                            pass
                    render = f"[文件 {name} (图片)]"
                    if desc:
                        render = f"{render}: {desc}"
                else:
                    async with httpx.AsyncClient(
                        timeout=30.0, proxy=None, trust_env=False,
                    ) as cl:
                        # 流式下载，最多读 _FILE_TEXT_MAX_BYTES+1 字节就停：群文件可能
                        # 几百 MB，resp.content 会把整个响应体读进内存撑爆进程。
                        # +1 只用于判定是否超限，内存上限仍约 _FILE_TEXT_MAX_BYTES。
                        read_limit = self._FILE_TEXT_MAX_BYTES + 1
                        async with cl.stream("GET", url) as resp:
                            if resp.status_code == 200:
                                chunks: list[bytes] = []
                                total = 0
                                async for chunk in resp.aiter_bytes():
                                    chunks.append(chunk)
                                    total += len(chunk)
                                    if total >= read_limit:
                                        break
                                payload = b"".join(chunks)
                                if b"\x00" in payload[:512]:
                                    render = f"[文件 {name} (二进制,无法读取)]"
                                else:
                                    text = payload[: self._FILE_TEXT_MAX_BYTES].decode(
                                        "utf-8", errors="replace"
                                    )
                                    truncated = total > self._FILE_TEXT_MAX_BYTES
                                    tail = "\n…(内容过长已截断)" if truncated else ""
                                    render = f"[文件 {name}]\n{text}{tail}"
                            else:
                                render = f"[文件 {name} (下载失败)]"
            except Exception:
                render = f"[文件 {name}]"
            if render:
                renders.append(render)

        if not renders:
            return

        raw = str(message.get("raw_message") or message.get("content") or "")
        replaced = [False]
        it = iter(renders)

        def _repl(m: Any) -> str:
            replaced[0] = True
            return next(it, m.group(0))

        new_raw = re.sub(r"\[CQ:file,[^\]]*\]", _repl, raw)
        if not replaced[0]:
            # content 里没有 CQ file 码（纯数组格式 raw_message 为空）→ 直接追加
            extra = " ".join(renders)
            new_raw = (
                (new_raw.strip() + " " + extra).strip()
                if new_raw.strip()
                else extra
            )
        message["raw_message"] = new_raw
        message["content"] = new_raw

    async def _process_incoming(self, raw_message: str):
        """处理一条来自 OneBot 客户端的消息"""
        message = json.loads(raw_message)

        # echo 匹配：这是对之前 call_action 的响应
        echo = message.get("echo")
        if echo and echo in self._pending_actions:
            future = self._pending_actions.pop(str(echo), None)
            if future and not future.done():
                future.set_result(message)
            return

        # 事件路由
        if message.get("post_type") == "message":
            msg_type = message.get("message_type")
            if msg_type in {"private", "group"}:
                # 标记需要后台拉取内容的段（不在 WS handler 中 await，避免死锁）
                reply_ids = self._expand_reply_segments(message)
                if reply_ids:
                    message["_pending_reply_ids"] = reply_ids
                unresolved = self._expand_forward_segments(message)
                if unresolved:
                    message["_pending_forward_ids"] = unresolved
                record_files = self._transcribe_record_segments(message)
                if record_files:
                    message["_pending_record_files"] = record_files
                file_segments = self._collect_file_segments(message)
                if file_segments:
                    message["_pending_file_ids"] = file_segments
                if not self._message_queue:
                    return
                try:
                    self._message_queue.put_nowait(message)
                except asyncio.QueueFull:
                    if self.logger:
                        self.logger.warning("Message queue full; dropping oldest message")
                    _ = self._message_queue.get_nowait()
                    self._message_queue.put_nowait(message)
                if self.logger:
                    if msg_type == "private":
                        self.logger.info(f"Queued private message from {message.get('user_id')}")
                    else:
                        self.logger.info(f"Queued group message from group {message.get('group_id')}, user {message.get('user_id')}")
        elif message.get("post_type") == "notice" and message.get("notice_type") == "notify" and message.get("sub_type") == "poke":
            # 戳一戳事件：入队以便自动回戳
            if not self._message_queue:
                return
            try:
                self._message_queue.put_nowait(message)
            except asyncio.QueueFull:
                pass
            if self.logger:
                self.logger.info(f"Queued poke notice: group {message.get('group_id')}, target {message.get('target_id')}, user {message.get('user_id')}")
        elif message.get("post_type") == "notice" and message.get("notice_type") == "group_ban":
            # 群禁言通知：bot 自己被禁言/解禁 或 全体禁言/解禁
            self._handle_group_ban_notice(message)

    async def receive_message(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """接收一条消息，返回标准化格式"""
        if not self._message_queue:
            return None
        try:
            raw_msg = await asyncio.wait_for(self._message_queue.get(), timeout=timeout)

            # 追踪自身 QQ 号
            self_id = raw_msg.get("self_id")
            if self_id:
                self._self_id = str(self_id)

            # 戳一戳通知事件
            if raw_msg.get("post_type") == "notice":
                return {
                    "message_type": "notice",
                    "channel": self.CHANNEL,
                    "notice_type": raw_msg.get("sub_type", ""),
                    "user_id": str(raw_msg.get("user_id") or ""),
                    "group_id": str(raw_msg.get("group_id") or ""),
                    "target_id": str(raw_msg.get("target_id") or ""),
                    "timestamp": raw_msg.get("time"),
                    "raw": raw_msg,
                }

            msg_type = raw_msg.get("message_type")
            sender_info = raw_msg.get("sender", {})
            user_nickname = sender_info.get("nickname") or sender_info.get("card") or None

            # 语音消息标注
            content = str(raw_msg.get("raw_message") or "")
            has_record = any(
                isinstance(s, dict) and s.get("type") == "record"
                for s in (raw_msg.get("message") or [])
                if isinstance(s, dict)
            )
            if has_record and not content.strip():
                content = "[语音]"
            elif has_record:
                content = f"[语音] {content}"

            result = {
                "message_type": msg_type,
                "channel": self.CHANNEL,
                "user_id": str(raw_msg.get("user_id")),
                "user_nickname": user_nickname,
                "content": content,
                "message_id": raw_msg.get("message_id"),
                "timestamp": raw_msg.get("time"),
                "raw": raw_msg,
                "attachments": self._extract_attachments(raw_msg),
            }

            # 传递后台拉取的标记字段（_process_incoming 设置，message_dispatcher 消费）
            reply_context = raw_msg.get("_reply_context", "").strip()
            if reply_context:
                result["_reply_context"] = reply_context
            for _key in ("_pending_reply_ids", "_pending_forward_ids", "_pending_record_files",
                         "_pending_file_ids", "_cached_reply_sender_id", "_forward_sub_count"):
                _val = raw_msg.get(_key)
                if _val is not None:
                    result[_key] = _val

            if msg_type == "group":
                interaction_context = self._extract_interaction_context(raw_msg)
                result["group_id"] = str(raw_msg.get("group_id"))
                result["quoted_message_id"] = interaction_context["quoted_message_id"]
                result["quoted_sender_id"] = interaction_context["quoted_sender_id"]
                result["mentioned_user_ids"] = interaction_context["mentioned_user_ids"]
                result["mentions_other_user"] = interaction_context["mentions_other_user"]
                result["mentions_all"] = interaction_context["mentions_all"]
                # 复用 _fetch_reply_content 已缓存的结果，避免重复 get_msg API 调用
                cached_reply_sender = raw_msg.get("_cached_reply_sender_id", "").strip()
                if cached_reply_sender and str(cached_reply_sender) == str(self._self_id or ""):
                    is_reply_to_bot = True
                else:
                    is_reply_to_bot = await self._is_reply_to_bot_message(
                        interaction_context["quoted_message_id"],
                    )
                result["is_at_bot"] = (
                    interaction_context["mentions_bot"]
                    or interaction_context["mentions_all"]
                )
                # is_reply_to_bot 单独存，不合入 is_at_bot：
                #   - 回复猫娘不应跳过缓冲（只有直接@才跳过）
                #   - Attention Gate 通过 is_direct_at 区分 @ 和回复
                result["is_reply_to_bot"] = is_reply_to_bot

            return result
        except asyncio.TimeoutError:
            return None

    def _check_at_bot(self, raw_msg: Dict[str, Any]) -> bool:
        """检查消息是否 @ 了机器人"""
        message = raw_msg.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if seg.get("type") == "at":
                    at_qq = seg.get("data", {}).get("qq")
                    if at_qq == "all":
                        return True
                    if str(at_qq) == str(raw_msg.get("self_id")):
                        return True
        return False

    _SENT_MSG_TTL_SECONDS = 3600  # 已发送消息 ID 缓存 1 小时

    def record_sent_message_id(self, message_id: str) -> None:
        """记录已发送的消息 ID（用于检测回复是否是回给 bot 的）"""
        mid = str(message_id or "").strip()
        if mid:
            import time
            self._sent_message_ids[mid] = time.time()
            self._cleanup_sent_message_cache()

    async def _is_reply_to_bot_message(self, quoted_message_id: str) -> bool:
        """通过 get_msg API 检查被引用的消息是否是 bot 发送的（兼容 KiraAI 方案）。"""
        qid = str(quoted_message_id or "").strip()
        if not qid:
            return False
        # 快速路径：本地缓存命中
        if qid in self._sent_message_ids:
            self._emit_log("DEBUG", f"[ReplyCheck] 缓存命中: msg_id={qid}")
            return True
        # API 查询（兜底：重启后本地缓存丢失也能正确判断）
        try:
            data = await self.get_msg(qid)
            sender_id = str(data.get("sender", {}).get("user_id") or data.get("user_id") or "")
            self._emit_log("DEBUG", f"[ReplyCheck] API查询: msg_id={qid} sender={sender_id} self={self._self_id}")
            if sender_id and sender_id == str(self._self_id or ""):
                self.record_sent_message_id(qid)  # 缓存结果
                return True
        except Exception as e:
            self._emit_log("DEBUG", f"[ReplyCheck] API失败: msg_id={qid} err={e}")
        return False

    def _cleanup_sent_message_cache(self) -> None:
        """清理过期的已发送消息 ID"""
        import time
        now = time.time()
        expired = [
            mid for mid, ts in self._sent_message_ids.items()
            if now - ts > self._SENT_MSG_TTL_SECONDS
        ]
        for mid in expired:
            del self._sent_message_ids[mid]

    # ── OneBot API 调用 ────────────────────────────────────────

    async def call_action(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
        if not self._main_client:
            raise RuntimeError("No OneBot client connected")
        # ServerConnection 没有 .open，用 close_code 判断
        if getattr(self._main_client, 'close_code', None) is not None:
            self._connected_clients.discard(self._main_client)
            self._main_client = next(iter(self._connected_clients), None)
            if not self._main_client:
                raise RuntimeError("No OneBot client connected")

        echo = secrets.token_hex(8)
        future = asyncio.get_running_loop().create_future()
        self._pending_actions[echo] = future
        payload = {
            "action": action,
            "params": params or {},
            "echo": echo,
        }
        try:
            await self._main_client.send(json.dumps(payload))
            if self.logger:
                self.logger.info(f"call_action sent: {action} echo={echo}")
        except Exception:
            self._pending_actions.pop(echo, None)
            if not future.done():
                future.cancel()
            raise
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            if self.logger:
                self.logger.info(f"call_action response: {action} status={response.get('status')}")
            if response.get("status") == "failed":
                raise RuntimeError(response.get("wording") or f"OneBot action failed: {action}")
            return response.get("data") or {}
        except asyncio.TimeoutError:
            self._emit_log("ERROR", f"call_action 超时: {action} (10秒未响应)")
            if self.logger:
                self.logger.warning(f"call_action timeout: {action} echo={echo}")
            raise
        finally:
            self._pending_actions.pop(echo, None)

    async def _fetch_login_info_async(self) -> None:
        """后台任务：获取登录信息并缓存（不阻塞消息处理）。"""
        try:
            await asyncio.sleep(0.5)
            await self.get_login_info()
        except Exception as e:
            self._emit_log("ERROR", f"获取账号信息失败: {e}")
            if self.logger:
                self.logger.warning(f"Background login info fetch failed: {e}")

    async def get_login_info(self) -> Dict[str, Any]:
        data = await self.call_action("get_login_info", timeout=5.0)
        uid = str(data.get("user_id") or "").strip()
        nick = str(data.get("nickname") or "").strip()
        if uid:
            self._self_id = uid
        if nick:
            self._self_nickname = nick
        if self.logger:
            self.logger.debug(f"get_login_info: self_id={uid}, nickname={nick}")
        return data

    async def get_friend_list(self) -> list[Dict[str, Any]]:
        data = await self.call_action("get_friend_list", timeout=10.0)
        return data if isinstance(data, list) else []

    async def get_group_list(self) -> list[Dict[str, Any]]:
        data = await self.call_action("get_group_list", timeout=10.0)
        return data if isinstance(data, list) else []

    async def send_message(self, user_id: str, message: str) -> Optional[str]:
        """发送私聊消息（CQ 码字符串），返回 message_id。

        与 segments 版同一个 action，只是 message 字段是字符串——编码不能
        改（把 CQ 码塞进 text 段会让 [CQ:at,qq=…] 原样显示给用户），但回执
        可以要：没有它就分不清"发出去了"和"没发出去"，投递确认链只能无条件
        判成功，未送达的回复会被当已投递清掉排除标、进 scoped 记忆。"""
        return await self._send_text_action(
            "send_private_msg", {"user_id": int(user_id), "message": message},
            log_target=f"private {user_id}",
        )

    async def send_group_message(self, group_id: str, message: str) -> Optional[str]:
        """发送群聊消息（CQ 码字符串），返回 message_id。见 send_message。"""
        return await self._send_text_action(
            "send_group_msg", {"group_id": int(group_id), "message": message},
            log_target=f"group {group_id}", record_sent=True,
        )

    async def _send_text_action(
        self, action: str, params: Dict[str, Any], *,
        log_target: str, record_sent: bool = False,
    ) -> Optional[str]:
        """One echo round-trip for the CQ-string senders (same plumbing as
        the segment senders; responses are dispatched by echo, not action)."""
        if not self._main_client:
            raise RuntimeError("No OneBot client connected")

        echo = secrets.token_hex(8)
        payload = {"action": action, "params": params, "echo": echo}
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_actions[echo] = future
        try:
            await self._main_client.send(json.dumps(payload))
            response = await asyncio.wait_for(future, timeout=10.0)
            message_id = str((response.get("data") or {}).get("message_id") or "")
            if message_id and record_sent:
                self.record_sent_message_id(message_id)
            if self.logger:
                self.logger.debug(f"Sent message to {log_target}")
            return message_id if message_id else None
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_actions.pop(echo, None)

    async def send_private_message_segments(self, user_id: str, segments: list[Dict[str, Any]], *, record_sent: bool = True) -> Optional[str]:
        """发送私聊消息片段，返回 message_id。"""
        if not self._main_client:
            raise RuntimeError("No OneBot client connected")

        echo = secrets.token_hex(8)
        payload = {
            "action": "send_private_msg",
            "params": {"user_id": int(user_id), "message": segments},
            "echo": echo,
        }
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_actions[echo] = future
        try:
            await self._main_client.send(json.dumps(payload))
            response = await asyncio.wait_for(future, timeout=10.0)
            message_id = str((response.get("data") or {}).get("message_id") or "")
            if message_id and record_sent:
                self.record_sent_message_id(message_id)
            return message_id if message_id else None
        except asyncio.TimeoutError:
            return None
        except Exception:
            raise
        finally:
            self._pending_actions.pop(echo, None)
        if self.logger:
            self.logger.debug(f"Sent segmented private message to {user_id}")

    async def send_private_record(self, user_id: str, file_uri: str, *, reply_message_id: str = ""):
        """发送私聊语音"""
        segments: list[Dict[str, Any]] = []
        if str(reply_message_id or "").strip():
            segments.append({"type": "reply", "data": {"id": str(reply_message_id)}})
        segments.append({"type": "record", "data": {"file": str(file_uri or "")}})
        return await self.send_private_message_segments(user_id, segments)

    async def send_group_record(self, group_id: str, file_uri: str, *, reply_message_id: str = "", at_user_id: str = ""):
        """发送群聊语音"""
        segments: list[Dict[str, Any]] = []
        if str(reply_message_id or "").strip():
            segments.append({"type": "reply", "data": {"id": str(reply_message_id)}})
        if str(at_user_id or "").strip():
            segments.append({"type": "at", "data": {"qq": str(at_user_id)}})
        segments.append({"type": "record", "data": {"file": str(file_uri or "")}})
        return await self.send_group_message_segments(group_id, segments)

    async def send_group_message_segments(self, group_id: str, segments: list[Dict[str, Any]], *, record_sent: bool = True, keyboard: str = "") -> Optional[str]:
        """发送群聊消息片段，返回 message_id"""
        if not self._main_client:
            raise RuntimeError("No OneBot client connected")

        echo = secrets.token_hex(8)
        payload = {
            "action": "send_group_msg",
            "params": {
                "group_id": int(group_id),
                "message": segments,
            },
            "echo": echo,
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_actions[echo] = future
        try:
            await self._main_client.send(json.dumps(payload))
            response = await asyncio.wait_for(future, timeout=10.0)
            message_id = str((response.get("data") or {}).get("message_id") or "")
            if message_id and record_sent:
                self.record_sent_message_id(message_id)
            return message_id if message_id else None
        except asyncio.TimeoutError:
            return None
        except Exception:
            raise
        finally:
            self._pending_actions.pop(echo, None)
        if self.logger:
            self.logger.debug(f"Sent segmented group message to {group_id}")

    async def send_group_poke(self, group_id: str, user_id: str) -> bool:
        """发送群聊戳一戳"""
        if not self._main_client:
            raise RuntimeError("No OneBot client connected")
        try:
            payload = {
                "action": "send_poke",
                "params": {
                    "group_id": int(group_id),
                    "user_id": int(user_id),
                },
            }
            await self._main_client.send(json.dumps(payload))
            if self.logger:
                self.logger.info(f"Sent poke to user {user_id} in group {group_id}")
            return True
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to send poke: {e}")
            return False

    async def get_record(self, file_id: str, output_format: str = "mp3") -> dict[str, Any]:
        """获取语音文件（返回 base64 编码的音频数据）。"""
        return await self.call_action("get_record", {"file": file_id, "out_format": output_format}, timeout=15.0)

    async def send_group_image(self, group_id: str, image_data: str, *, reply_message_id: str = "", at_user_id: str = "", sub_type: str = "") -> Optional[str]:
        """发送群聊图片

        Args:
            group_id: 群号
            image_data: 图片 URL、base64 字符串（带 base64:// 前缀）或本地文件路径
            sub_type: 图片子类型，"1"=表情包贴纸（非普通图片）
        """
        if not self._main_client:
            raise RuntimeError("No OneBot client connected")
        segments: list[Dict[str, Any]] = []
        if str(reply_message_id or "").strip():
            segments.append({"type": "reply", "data": {"id": str(reply_message_id)}})
        if str(at_user_id or "").strip():
            segments.append({"type": "at", "data": {"qq": str(at_user_id)}})
        img_data: dict[str, str] = {"file": str(image_data)}
        if sub_type:
            img_data["sub_type"] = sub_type
        segments.append({"type": "image", "data": img_data})
        return await self.send_group_message_segments(group_id, segments, record_sent=False)

    async def send_private_image(self, user_id: str, image_data: str) -> Optional[str]:
        """发送私聊图片"""
        if not self._main_client:
            raise RuntimeError("No OneBot client connected")
        segments: list[Dict[str, Any]] = [
            {"type": "image", "data": {"file": str(image_data)}}
        ]
        return await self.send_private_message_segments(user_id, segments)

    # ── 消息操作 ────────────────────────────────────────────────

    async def set_msg_emoji_like(self, message_id: str, emoji_id: str) -> Dict[str, Any]:
        """对消息设置表情表态。"""
        return await self.call_action("set_msg_emoji_like", {"message_id": int(message_id), "emoji_id": str(emoji_id)}, timeout=5.0)

    async def delete_msg(self, message_id: str) -> Dict[str, Any]:
        """撤回消息。"""
        return await self.call_action("delete_msg", {"message_id": int(message_id)}, timeout=5.0)

    async def get_msg(self, message_id: str) -> Dict[str, Any]:
        """获取消息详情。"""
        return await self.call_action("get_msg", {"message_id": int(message_id)}, timeout=10.0)

    async def get_forward_msg(self, forward_id: str) -> Dict[str, Any]:
        """获取合并转发消息内容。"""
        return await self.call_action("get_forward_msg", {"id": str(forward_id)}, timeout=5.0)

    # ── 好友操作 ────────────────────────────────────────────────

    async def send_like(self, user_id: str, times: int = 1) -> Dict[str, Any]:
        """给好友点赞。"""
        return await self.call_action("send_like", {"user_id": int(user_id), "times": max(1, int(times))}, timeout=5.0)

    async def set_friend_add_request(self, flag: str, approve: bool, remark: str = "") -> Dict[str, Any]:
        """处理加好友请求。"""
        return await self.call_action("set_friend_add_request", {"flag": str(flag), "approve": bool(approve), "remark": str(remark or "")}, timeout=5.0)

    # ── 群管理操作 ──────────────────────────────────────────────

    async def set_group_kick(self, group_id: str, user_id: str, reject_add_request: bool = False) -> Dict[str, Any]:
        """群聊踢人。"""
        return await self.call_action("set_group_kick", {"group_id": int(group_id), "user_id": int(user_id), "reject_add_request": bool(reject_add_request)}, timeout=5.0)

    async def set_group_ban(self, group_id: str, user_id: str, duration: int = 1800) -> Dict[str, Any]:
        """群聊单人禁言（duration 单位秒，0 表示取消禁言）。"""
        return await self.call_action("set_group_ban", {"group_id": int(group_id), "user_id": int(user_id), "duration": int(duration)}, timeout=5.0)

    async def set_group_whole_ban(self, group_id: str, enable: bool = True) -> Dict[str, Any]:
        """群聊全员禁言。"""
        return await self.call_action("set_group_whole_ban", {"group_id": int(group_id), "enable": bool(enable)}, timeout=5.0)

    async def set_group_admin(self, group_id: str, user_id: str, enable: bool = True) -> Dict[str, Any]:
        """设置/取消群管理员。"""
        return await self.call_action("set_group_admin", {"group_id": int(group_id), "user_id": int(user_id), "enable": bool(enable)}, timeout=5.0)

    async def set_group_card(self, group_id: str, user_id: str, card: str = "") -> Dict[str, Any]:
        """设置群名片（群备注）。"""
        return await self.call_action("set_group_card", {"group_id": int(group_id), "user_id": int(user_id), "card": str(card or "")}, timeout=5.0)

    async def set_group_name(self, group_id: str, group_name: str) -> Dict[str, Any]:
        """设置群名称。"""
        return await self.call_action("set_group_name", {"group_id": int(group_id), "group_name": str(group_name)}, timeout=5.0)

    async def set_group_leave(self, group_id: str, is_dismiss: bool = False) -> Dict[str, Any]:
        """退出群聊或解散群。"""
        return await self.call_action("set_group_leave", {"group_id": int(group_id), "is_dismiss": bool(is_dismiss)}, timeout=5.0)

    async def set_group_special_title(self, group_id: str, user_id: str, special_title: str = "", duration: int = -1) -> Dict[str, Any]:
        """设置群专属头衔（duration 单位秒，-1 表示永久）。"""
        return await self.call_action("set_group_special_title", {"group_id": int(group_id), "user_id": int(user_id), "special_title": str(special_title), "duration": int(duration)}, timeout=5.0)

    async def set_group_add_request(self, flag: str, sub_type: str, approve: bool, reason: str = "") -> Dict[str, Any]:
        """处理加群请求/邀请。"""
        return await self.call_action("set_group_add_request", {"flag": str(flag), "sub_type": str(sub_type), "approve": bool(approve), "reason": str(reason or "")}, timeout=5.0)

    # ── 信息获取 ────────────────────────────────────────────────

    async def get_stranger_info(self, user_id: str, no_cache: bool = False) -> Dict[str, Any]:
        """获取陌生人信息。"""
        return await self.call_action("get_stranger_info", {"user_id": int(user_id), "no_cache": bool(no_cache)}, timeout=5.0)

    async def get_group_info(self, group_id: str, no_cache: bool = False) -> Dict[str, Any]:
        """获取群信息。"""
        return await self.call_action("get_group_info", {"group_id": int(group_id), "no_cache": bool(no_cache)}, timeout=5.0)

    async def get_group_member_info(self, group_id: str, user_id: str, no_cache: bool = False) -> Dict[str, Any]:
        """获取群成员信息。"""
        return await self.call_action("get_group_member_info", {"group_id": int(group_id), "user_id": int(user_id), "no_cache": bool(no_cache)}, timeout=5.0)

    async def get_group_member_list(self, group_id: str, no_cache: bool = False) -> list[Dict[str, Any]]:
        """获取群成员列表。"""
        data = await self.call_action("get_group_member_list", {"group_id": int(group_id), "no_cache": bool(no_cache)}, timeout=10.0)
        return data if isinstance(data, list) else []

    async def get_group_honor_info(self, group_id: str, type: str = "all") -> Dict[str, Any]:
        """获取群荣誉信息。"""
        return await self.call_action("get_group_honor_info", {"group_id": int(group_id), "type": str(type)}, timeout=5.0)

    # ── Cookies / 凭证 ──────────────────────────────────────────

    async def get_cookies(self, domain: str = "") -> Dict[str, Any]:
        """获取 cookies。"""
        return await self.call_action("get_cookies", {"domain": str(domain)}, timeout=5.0)

    async def get_csrf_token(self) -> Dict[str, Any]:
        """获取 CSRF token。"""
        return await self.call_action("get_csrf_token", timeout=5.0)

    async def get_credentials(self, domain: str = "") -> Dict[str, Any]:
        """获取 QQ 相关凭证。"""
        return await self.call_action("get_credentials", {"domain": str(domain)}, timeout=5.0)

    # ── 资源获取 ────────────────────────────────────────────────

    async def get_image(self, file: str) -> Dict[str, Any]:
        """获取图片文件数据。"""
        return await self.call_action("get_image", {"file": str(file)}, timeout=10.0)

    async def can_send_image(self) -> Dict[str, Any]:
        """检查是否可以发送图片。"""
        return await self.call_action("can_send_image", timeout=5.0)

    async def can_send_record(self) -> Dict[str, Any]:
        """检查是否可以发送语音。"""
        return await self.call_action("can_send_record", timeout=5.0)

    # ── 状态 / 版本 ─────────────────────────────────────────────

    async def get_status(self) -> Dict[str, Any]:
        """获取运行状态。"""
        return await self.call_action("get_status", timeout=5.0)

    async def get_version_info(self) -> Dict[str, Any]:
        """获取版本信息。"""
        return await self.call_action("get_version_info", timeout=5.0)

    async def clean_cache(self) -> Dict[str, Any]:
        """清理缓存。"""
        return await self.call_action("clean_cache", timeout=5.0)

    # ── 账号设置 ────────────────────────────────────────────────

    async def set_qq_profile(self, nickname: str = "", company: str = "", email: str = "", college: str = "", personal_note: str = "") -> Dict[str, Any]:
        """设置 QQ 个人资料。"""
        return await self.call_action("set_qq_profile", {"nickname": str(nickname or ""), "company": str(company or ""), "email": str(email or ""), "college": str(college or ""), "personal_note": str(personal_note or "")}, timeout=5.0)

    async def set_qq_avatar(self, file: str) -> Dict[str, Any]:
        """设置 QQ 头像。"""
        return await self.call_action("set_qq_avatar", {"file": str(file)}, timeout=10.0)

    async def set_self_longnick(self, longnick: str) -> Dict[str, Any]:
        """设置自身个性签名。"""
        return await self.call_action("set_self_longnick", {"longnick": str(longnick)}, timeout=5.0)

    async def set_online_status(self, status: int) -> Dict[str, Any]:
        """设置在线状态。"""
        return await self.call_action("set_online_status", {"status": int(status)}, timeout=5.0)

    async def get_online_clients(self) -> Dict[str, Any]:
        """获取当前在线客户端列表。"""
        return await self.call_action("get_online_clients", timeout=5.0)

    async def get_robot_uin_range(self) -> Dict[str, Any]:
        """获取机器人 UIN 范围。"""
        return await self.call_action("get_robot_uin_range", timeout=5.0)

    # ── 好友管理 ────────────────────────────────────────────────

    async def delete_friend(self, user_id: str) -> Dict[str, Any]:
        """删除好友。"""
        return await self.call_action("delete_friend", {"user_id": int(user_id)}, timeout=5.0)

    async def get_friends_with_category(self) -> Dict[str, Any]:
        """获取带分组的好友列表。"""
        return await self.call_action("get_friends_with_category", timeout=10.0)

    async def friend_poke(self, user_id: str) -> Dict[str, Any]:
        """好友戳一戳。"""
        return await self.call_action("friend_poke", {"user_id": int(user_id)}, timeout=5.0)

    async def get_profile_like(self) -> Dict[str, Any]:
        """获取自身点赞列表。"""
        return await self.call_action("get_profile_like", timeout=5.0)

    # ── 消息已读 ────────────────────────────────────────────────

    async def mark_msg_as_read(self, message_id: str) -> Dict[str, Any]:
        """标记消息为已读。"""
        return await self.call_action("mark_msg_as_read", {"message_id": int(message_id)}, timeout=5.0)

    async def mark_private_msg_as_read(self, user_id: str) -> Dict[str, Any]:
        """标记私聊消息为已读。"""
        return await self.call_action("mark_private_msg_as_read", {"user_id": int(user_id)}, timeout=5.0)

    async def mark_group_msg_as_read(self, group_id: str) -> Dict[str, Any]:
        """标记群聊消息为已读。"""
        return await self.call_action("mark_group_msg_as_read", {"group_id": int(group_id)}, timeout=5.0)

    async def _mark_all_as_read(self) -> Dict[str, Any]:
        """标记所有消息为已读。"""
        return await self.call_action("_mark_all_as_read", timeout=5.0)

    # ── 合并转发 ────────────────────────────────────────────────

    async def send_group_forward_msg(self, group_id: str, messages: list[Dict[str, Any]]) -> Dict[str, Any]:
        """发送群聊合并转发消息。"""
        return await self.call_action("send_group_forward_msg", {"group_id": int(group_id), "messages": messages}, timeout=10.0)

    async def send_private_forward_msg(self, user_id: str, messages: list[Dict[str, Any]]) -> Dict[str, Any]:
        """发送私聊合并转发消息。"""
        return await self.call_action("send_private_forward_msg", {"user_id": int(user_id), "messages": messages}, timeout=10.0)

    async def send_forward_msg(self, message_type: str, user_id: str = "", group_id: str = "", messages: Optional[list[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """发送合并转发消息（通用）。"""
        params: Dict[str, Any] = {"message_type": str(message_type), "messages": messages or []}
        if user_id:
            params["user_id"] = int(user_id)
        if group_id:
            params["group_id"] = int(group_id)
        return await self.call_action("send_forward_msg", params, timeout=10.0)

    async def forward_friend_single_msg(self, user_id: str, message_id: str) -> Dict[str, Any]:
        """转发单条好友消息。"""
        return await self.call_action("forward_friend_single_msg", {"user_id": int(user_id), "message_id": int(message_id)}, timeout=5.0)

    async def forward_group_single_msg(self, group_id: str, message_id: str) -> Dict[str, Any]:
        """转发单条群消息。"""
        return await self.call_action("forward_group_single_msg", {"group_id": int(group_id), "message_id": int(message_id)}, timeout=5.0)

    # ── 消息历史 / 输入状态 / 最近联系人 ───────────────────────

    async def get_friend_msg_history(self, user_id: str, message_seq: int = 0, count: int = 20) -> Dict[str, Any]:
        """获取好友消息历史。"""
        return await self.call_action("get_friend_msg_history", {"user_id": int(user_id), "message_seq": int(message_seq), "count": int(count)}, timeout=10.0)

    async def get_group_msg_history(self, group_id: str, message_seq: int = 0, count: int = 20) -> Dict[str, Any]:
        """获取群消息历史。"""
        return await self.call_action("get_group_msg_history", {"group_id": int(group_id), "message_seq": int(message_seq), "count": int(count)}, timeout=10.0)

    async def set_input_status(self, user_id: str = "", group_id: str = "", event_type: int = 1) -> Dict[str, Any]:
        """设置输入状态（1: 正在输入, 2: 取消输入）。"""
        params: Dict[str, Any] = {"event_type": int(event_type)}
        if user_id:
            params["user_id"] = int(user_id)
        if group_id:
            params["group_id"] = int(group_id)
        return await self.call_action("set_input_status", params, timeout=5.0)

    async def get_recent_contact(self) -> Dict[str, Any]:
        """获取最近联系人列表。"""
        return await self.call_action("get_recent_contact", timeout=10.0)

    # ── 群系统消息 / 公告 ──────────────────────────────────────

    async def get_group_system_msg(self) -> Dict[str, Any]:
        """获取群系统消息。"""
        return await self.call_action("get_group_system_msg", timeout=5.0)

    async def _send_group_notice(self, group_id: str, content: str, image: str = "") -> Dict[str, Any]:
        """发送群公告。"""
        return await self.call_action("_send_group_notice", {"group_id": int(group_id), "content": str(content), "image": str(image)}, timeout=5.0)

    async def _get_group_notice(self, group_id: str) -> Dict[str, Any]:
        """获取群公告。"""
        return await self.call_action("_get_group_notice", {"group_id": int(group_id)}, timeout=5.0)

    async def _del_group_notice(self, group_id: str, notice_id: str) -> Dict[str, Any]:
        """删除群公告。"""
        return await self.call_action("_del_group_notice", {"group_id": int(group_id), "notice_id": str(notice_id)}, timeout=5.0)

    async def get_group_at_all_remain(self, group_id: str) -> Dict[str, Any]:
        """获取群 @全体成员 剩余次数。"""
        return await self.call_action("get_group_at_all_remain", {"group_id": int(group_id)}, timeout=5.0)

    async def get_group_ignore_add_request(self, group_id: str) -> Dict[str, Any]:
        """获取群忽略加群请求列表。"""
        return await self.call_action("get_group_ignore_add_request", {"group_id": int(group_id)}, timeout=5.0)

    async def get_group_shut_list(self, group_id: str) -> list[Dict[str, Any]]:
        """获取群禁言列表。"""
        data = await self.call_action("get_group_shut_list", {"group_id": int(group_id)}, timeout=5.0)
        return data if isinstance(data, list) else []

    # ── 群签到 / 头像 ───────────────────────────────────────────

    async def set_group_sign(self, group_id: str, sign: str = "") -> Dict[str, Any]:
        """设置群签到。"""
        return await self.call_action("set_group_sign", {"group_id": int(group_id), "sign": str(sign)}, timeout=5.0)

    async def send_group_sign(self, group_id: str) -> Dict[str, Any]:
        """执行群签到。"""
        return await self.call_action("send_group_sign", {"group_id": int(group_id)}, timeout=5.0)

    async def set_group_portrait(self, group_id: str, file: str, is_set: bool = True) -> Dict[str, Any]:
        """设置群头像。"""
        return await self.call_action("set_group_portrait", {"group_id": int(group_id), "file": str(file), "is_set": bool(is_set)}, timeout=10.0)

    async def get_group_info_ex(self, group_id: str) -> Dict[str, Any]:
        """获取扩展群信息。"""
        return await self.call_action("get_group_info_ex", {"group_id": int(group_id)}, timeout=5.0)

    # ── 精华消息 ────────────────────────────────────────────────

    async def get_essence_msg_list(self, group_id: str) -> Dict[str, Any]:
        """获取精华消息列表。"""
        return await self.call_action("get_essence_msg_list", {"group_id": int(group_id)}, timeout=5.0)

    async def set_essence_msg(self, message_id: str) -> Dict[str, Any]:
        """设置精华消息。"""
        return await self.call_action("set_essence_msg", {"message_id": int(message_id)}, timeout=5.0)

    async def delete_essence_msg(self, message_id: str) -> Dict[str, Any]:
        """移除精华消息。"""
        return await self.call_action("delete_essence_msg", {"message_id": int(message_id)}, timeout=5.0)

    # ── 群文件 ──────────────────────────────────────────────────

    async def upload_group_file(self, group_id: str, file: str, name: str = "", folder: str = "") -> Dict[str, Any]:
        """上传群文件。"""
        return await self.call_action("upload_group_file", {"group_id": int(group_id), "file": str(file), "name": str(name), "folder": str(folder)}, timeout=30.0)

    async def delete_group_file(self, group_id: str, file_id: str, busid: int = 0) -> Dict[str, Any]:
        """删除群文件。"""
        return await self.call_action("delete_group_file", {"group_id": int(group_id), "file_id": str(file_id), "busid": int(busid)}, timeout=5.0)

    async def create_group_file_folder(self, group_id: str, name: str) -> Dict[str, Any]:
        """创建群文件文件夹。"""
        return await self.call_action("create_group_file_folder", {"group_id": int(group_id), "name": str(name)}, timeout=5.0)

    async def delete_group_folder(self, group_id: str, folder_id: str) -> Dict[str, Any]:
        """删除群文件文件夹。"""
        return await self.call_action("delete_group_folder", {"group_id": int(group_id), "folder_id": str(folder_id)}, timeout=5.0)

    async def get_group_file_system_info(self, group_id: str) -> Dict[str, Any]:
        """获取群文件系统信息。"""
        return await self.call_action("get_group_file_system_info", {"group_id": int(group_id)}, timeout=5.0)

    async def get_group_root_files(self, group_id: str) -> Dict[str, Any]:
        """获取群根目录文件列表。"""
        return await self.call_action("get_group_root_files", {"group_id": int(group_id)}, timeout=10.0)

    async def get_group_files_by_folder(self, group_id: str, folder_id: str) -> Dict[str, Any]:
        """获取群子目录文件列表。"""
        return await self.call_action("get_group_files_by_folder", {"group_id": int(group_id), "folder_id": str(folder_id)}, timeout=10.0)

    async def get_group_file_url(self, group_id: str, file_id: str, busid: int = 0) -> Dict[str, Any]:
        """获取群文件下载链接。"""
        return await self.call_action("get_group_file_url", {"group_id": int(group_id), "file_id": str(file_id), "busid": int(busid)}, timeout=5.0)

    async def get_private_file_url(self, user_id: str, file_id: str) -> Dict[str, Any]:
        """获取私聊文件下载链接（NapCat 要求同时带 user_id 与 file_id）。"""
        return await self.call_action(
            "get_private_file_url",
            {"user_id": int(user_id), "file_id": str(file_id)},
            timeout=5.0,
        )

    async def upload_private_file(self, user_id: str, file: str, name: str = "") -> Dict[str, Any]:
        """上传私聊文件。"""
        return await self.call_action("upload_private_file", {"user_id": int(user_id), "file": str(file), "name": str(name)}, timeout=30.0)

    async def download_file(self, url: str, thread_count: int = 3, headers: Optional[list[str]] = None) -> Dict[str, Any]:
        """下载文件到本地。"""
        return await self.call_action("download_file", {"url": str(url), "thread_count": int(thread_count), "headers": headers or []}, timeout=60.0)

    async def get_file(self, url: str, thread_count: int = 3, headers: Optional[list[str]] = None) -> Dict[str, Any]:
        """获取文件数据。"""
        return await self.call_action("get_file", {"url": str(url), "thread_count": int(thread_count), "headers": headers or []}, timeout=60.0)

    async def get_file_by_id(self, file_id: str) -> Dict[str, Any]:
        """通过 file_id 获取文件信息（OneBot v11 标准 ``get_file``）。

        用于群文件消息只有 ``file_id``、没有 ``busid`` 时替代
        ``get_group_file_url``（NapCat 的 get_group_file_url 需要真实 busid，
        传 0 会被拒）。
        """
        return await self.call_action("get_file", {"file_id": str(file_id)}, timeout=5.0)

    # ── AI / OCR / 翻译 ─────────────────────────────────────────

    async def ocr_image(self, image: str) -> Dict[str, Any]:
        """图片 OCR 识别。"""
        return await self.call_action("ocr_image", {"image": str(image)}, timeout=10.0)

    async def check_url_safely(self, url: str) -> Dict[str, Any]:
        """检查链接安全性。"""
        return await self.call_action("check_url_safely", {"url": str(url)}, timeout=5.0)

    async def translate_en2zh(self, words: str) -> Dict[str, Any]:
        """英文翻译为中文。"""
        return await self.call_action("translate_en2zh", {"words": str(words)}, timeout=5.0)

    async def fetch_custom_face(self, count: int = 10) -> Dict[str, Any]:
        """获取收藏表情列表。"""
        return await self.call_action("fetch_custom_face", {"count": int(count)}, timeout=5.0)

    async def fetch_emoji_like(self, message_id: str, emoji_id: str, emoji_type: str = "", set: bool = True) -> Dict[str, Any]:
        """获取消息表情贴表情列表。"""
        return await self.call_action("fetch_emoji_like", {"message_id": int(message_id), "emoji_id": str(emoji_id), "emoji_type": str(emoji_type), "set": bool(set)}, timeout=5.0)

    async def create_collection(self, rawdata: str, brief: str = "") -> Dict[str, Any]:
        """创建收藏。"""
        return await self.call_action("create_collection", {"rawdata": str(rawdata), "brief": str(brief)}, timeout=5.0)

    async def get_collection_list(self, category: int = 0) -> Dict[str, Any]:
        """获取收藏列表。"""
        return await self.call_action("get_collection_list", {"category": int(category)}, timeout=5.0)

    # ── 模型展示 ────────────────────────────────────────────────

    async def _get_model_show(self, model: str) -> Dict[str, Any]:
        """获取模型展示配置。"""
        return await self.call_action("_get_model_show", {"model": str(model)}, timeout=5.0)

    async def _set_model_show(self, model: str, model_show: str) -> Dict[str, Any]:
        """设置模型展示。"""
        return await self.call_action("_set_model_show", {"model": str(model), "model_show": str(model_show)}, timeout=5.0)

    # ── NapCat 扩展 ─────────────────────────────────────────────

    async def ArkSharePeer(self, user_id: str, ark_json: str) -> Dict[str, Any]:
        """分享 ARK 消息给好友。"""
        return await self.call_action("ArkSharePeer", {"user_id": int(user_id), "ark_json": str(ark_json)}, timeout=10.0)

    async def ArkShareGroup(self, group_id: str, ark_json: str) -> Dict[str, Any]:
        """分享 ARK 消息到群。"""
        return await self.call_action("ArkShareGroup", {"group_id": int(group_id), "ark_json": str(ark_json)}, timeout=10.0)

    async def handle_quick_operation(self, context: Dict[str, Any], operation: Dict[str, Any]) -> Dict[str, Any]:
        """处理快速操作。"""
        return await self.call_action(".handle_quick_operation", {"context": context, "operation": operation}, timeout=5.0)

    async def get_mini_app_ark(self, appid: str = "", app_type: str = "", app_path: str = "", title: str = "", desc: str = "", pic_url: str = "", jump_url: str = "", scene: int = 0) -> Dict[str, Any]:
        """获取小程序 ARK 消息。"""
        return await self.call_action("get_mini_app_ark", {"appid": str(appid), "app_type": str(app_type), "app_path": str(app_path), "title": str(title), "desc": str(desc), "pic_url": str(pic_url), "jump_url": str(jump_url), "scene": int(scene)}, timeout=10.0)

    async def nc_get_packet_status(self) -> Dict[str, Any]:
        """获取包状态。"""
        return await self.call_action("nc_get_packet_status", timeout=5.0)

    async def nc_get_user_status(self, user_id: str) -> Dict[str, Any]:
        """获取用户状态。"""
        return await self.call_action("nc_get_user_status", {"user_id": int(user_id)}, timeout=5.0)

    async def nc_get_rkey(self) -> Dict[str, Any]:
        """获取 rkey。"""
        return await self.call_action("nc_get_rkey", timeout=5.0)

    # ── AI 语音 ─────────────────────────────────────────────────

    async def get_ai_record(self, group_id: str, character_id: str, text: str) -> Dict[str, Any]:
        """获取 AI 语音。"""
        return await self.call_action("get_ai_record", {"group_id": int(group_id), "character_id": str(character_id), "text": str(text)}, timeout=30.0)

    async def get_ai_characters(self, group_id: str, chat_type: int = 1) -> Dict[str, Any]:
        """获取 AI 角色列表。"""
        return await self.call_action("get_ai_characters", {"group_id": int(group_id), "chat_type": int(chat_type)}, timeout=10.0)

    async def send_group_ai_record(self, group_id: str, character_id: str, text: str) -> Dict[str, Any]:
        """发送群聊 AI 语音。"""
        return await self.call_action("send_group_ai_record", {"group_id": int(group_id), "character_id": str(character_id), "text": str(text)}, timeout=30.0)

    async def group_poke(self, group_id: str, user_id: str) -> Dict[str, Any]:
        """群聊戳一戳（通过 API）。"""
        return await self.call_action("group_poke", {"group_id": int(group_id), "user_id": int(user_id)}, timeout=5.0)
