"""QQ 开放平台连接器 — 官方 QQ Bot API"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import httpx
import websockets

from .qq_connection import QQConnectionBase


class QQOpenPlatformConnection(QQConnectionBase):
    """QQ 开放平台官方 Bot API 连接

    WebSocket 事件 → 内部统一消息格式 → 上层管道
    HTTP API → 发送消息
    """

    _API_BASE = "https://api.sgroup.qq.com"
    _TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

    def __init__(
        self,
        *,
        app_id: str,
        client_secret: str,
        logger: Any = None,
        message_queue_size: int = 100,
    ):
        self._app_id = str(app_id or "").strip()
        self._client_secret = str(client_secret or "").strip()
        self.token = ""
        self.logger = logger
        self.ws = None
        self._ws = None
        self._http = None
        self._access_token = ""
        self._token_expires_at: float = 0
        self._heartbeat_task = None
        self._receive_task = None
        self._heartbeat_interval: float = 30.0
        self._closing = False
        self._self_id = ""
        self._self_nickname = ""
        self._last_seq = 0
        self._session_id = ""  # Resume 重连所需
        self._sent_message_ids: dict[str, float] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, message_queue_size))

    @property
    def needs_attention(self) -> bool:
        return False  # 开放平台只收 @bot，无需注意力竞争

    @property
    def supports_voice(self) -> bool:
        return False  # 开放平台不支持语音消息

    @property
    def supports_poke(self) -> bool:
        return False  # 开放平台不支持戳一戳

    @property
    def receives_all_messages(self) -> bool:
        return False  # 开放平台仅接收 @bot 消息

    async def get_login_info(self) -> dict[str, Any]:
        return {"user_id": self._self_id, "nickname": self._self_nickname}

    async def get_friend_list(self) -> list[dict[str, Any]]:
        return []

    async def get_group_list(self) -> list[dict[str, Any]]:
        return []

    # ==========================================
    # 连接生命周期
    # ==========================================

    async def connect(self) -> None:
        if not self._app_id or not self._client_secret:
            raise RuntimeError("QQ 开放平台: app_id 和 client_secret 未配置")
        self._closing = False
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        await self._refresh_token()
        if self.logger:
            self.logger.info(f"[QQOpenPlatform] token 已获取")
        ws_url = await self._get_gateway_url()
        if self.logger:
            self.logger.info(f"[QQOpenPlatform] 连接网关: {ws_url[:60]}...")
        self._ws = await websockets.connect(ws_url, max_size=2 ** 23)
        self.ws = self._ws
        if self.logger:
            self.logger.info("[QQOpenPlatform] WebSocket 已连接")
        await self._handshake(is_reconnect=False)
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _handshake(self, *, is_reconnect: bool) -> None:
        """WebSocket 握手：Hello → [Resume] → Identify → READY"""
        # Hello
        raw = await self._ws.recv()
        hello = json.loads(raw)
        if hello.get("op") == 10:
            self._heartbeat_interval = max(10.0, float(hello["d"]["heartbeat_interval"]) / 1000.0 - 2.0)
            if self.logger:
                self.logger.info(f"[QQOpenPlatform] Hello 收到, 心跳间隔: {self._heartbeat_interval:.0f}s")
        # 重连优先 Resume，失败再 Identify
        if is_reconnect and self._session_id:
            await self._ws.send(json.dumps({
                "op": 6, "d": {"token": f"QQBot {self._access_token}",
                                "session_id": self._session_id,
                                "seq": self._last_seq},
            }))
            try:
                resp = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
                event = json.loads(resp)
                if event.get("op") == 0 and event.get("t") == "RESUMED":
                    if self.logger:
                        self.logger.info("[QQOpenPlatform] Resume 成功，事件已补发")
                    return
                if self.logger:
                    self.logger.warning(f"[QQOpenPlatform] Resume 失败(op={event.get('op')} t={event.get('t')})，回退 Identify")
            except asyncio.TimeoutError:
                if self.logger:
                    self.logger.warning("[QQOpenPlatform] Resume 超时，回退 Identify")
        # Identify
        await self._ws.send(json.dumps({
            "op": 2, "d": {
                "token": f"QQBot {self._access_token}",
                "intents": (1 << 25) | (1 << 12),
                "shard": [0, 1],
            },
        }))
        resp = await self._ws.recv()
        ready = json.loads(resp)
        if ready.get("op") == 0 and ready.get("t") == "READY":
            user = ready["d"].get("user") or {}
            self._self_id = str(user.get("id") or "")
            self._self_nickname = str(user.get("username") or "")
            self._session_id = str(ready["d"].get("session_id") or "")
            if self.logger:
                self.logger.info(f"[QQOpenPlatform] 已就绪: {self._self_nickname} ({self._self_id})")
        else:
            raise RuntimeError(f"鉴权失败: op={ready.get('op')} t={ready.get('t')}")

    async def disconnect(self) -> None:
        self._closing = True
        for task in [self._heartbeat_task, self._receive_task]:
            if task and not task.done():
                task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
            self.ws = None
        if self._http:
            await self._http.aclose()
            self._http = None

    def is_connected(self) -> bool:
        return self._ws is not None

    # ==========================================
    # 消息接收
    # ==========================================

    async def _receive_loop(self) -> None:
        while not self._closing:
            if not self._ws:
                await asyncio.sleep(1)
                continue
            try:
                raw = await self._ws.recv()
                payload = json.loads(raw)
                op = payload.get("op")
                if op == 0:  # Dispatch
                    self._last_seq = payload.get("s", self._last_seq)
                    event_type = payload.get("t", "")
                    if event_type in ("GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"):
                        msg = self._convert_event(event_type, payload["d"])
                        if msg:
                            try:
                                self._message_queue.put_nowait(msg)
                            except asyncio.QueueFull:
                                self._message_queue.get_nowait()
                                self._message_queue.put_nowait(msg)
                    continue  # 成功，跳过重连
                elif op == 1:  # Heartbeat
                    await self._ws.send(json.dumps({"op": 11, "d": self._last_seq}))
                    continue  # 成功，跳过重连
                elif op == 11:  # Heartbeat ACK → 忽略
                    continue
                elif op == 7:  # Reconnect → 关闭当前连接，由下方 _try_reconnect() 重建
                    if self.logger:
                        self.logger.warning("[QQOpenPlatform] 服务端要求重连")
                    if self._ws:
                        try: await self._ws.close()
                        except Exception: pass
                    self._ws = None
                    self.ws = None
                # op==7 及其他未知 op → 不 continue，自然落到重连逻辑
            except websockets.ConnectionClosed:
                if self.logger:
                    self.logger.warning("[QQOpenPlatform] WebSocket 断开")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[QQOpenPlatform] 接收异常: {e}")
            # 断连 → 重连
            if not self._closing:
                await self._try_reconnect()

    async def receive_message(self, timeout: float = 1.0) -> Optional[dict[str, Any]]:
        try:
            return await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ==========================================
    # 消息发送
    # ==========================================

    async def send_group_message_segments(
        self, group_id: str, segments: list[dict[str, Any]], *, record_sent: bool = True, keyboard: str = ""
    ) -> Optional[str]:
        """将 OneBot segments 转换为 QQ 开放平台格式并发送"""
        content_parts: list[str] = []
        reply_msg_id = ""
        at_user_id = ""
        image_url = ""

        for seg in segments:
            seg_type = str(seg.get("type") or "").strip()
            data = seg.get("data") or {}
            if seg_type == "reply":
                reply_msg_id = str(data.get("id") or "")
            elif seg_type == "at":
                at_user_id = str(data.get("qq") or "")
                content_parts.append(f"<@!{at_user_id}>")
            elif seg_type == "text":
                content_parts.append(str(data.get("text") or ""))
            elif seg_type == "image":
                image_url = str(data.get("file") or "")
            elif seg_type == "face":
                # 小表情 → 文本占位
                content_parts.append(f"[表情{data.get('id','')}]")
            elif seg_type == "record":
                content_parts.append("[语音消息]")

        content = "".join(content_parts).strip()
        if not content and not image_url:
            return None

        await self._ensure_token()
        body: dict[str, Any] = {}
        # 群图片需要先上传获取 file_info，再用 msg_type=7 + media 发送
        if image_url:
            file_info = await self._upload_group_image(group_id, image_url)
            if file_info:
                body["msg_type"] = 7
                body["media"] = {"file_info": file_info}
                if content:
                    body["content"] = content
            else:
                # 上传失败 → 降级为文本
                if not content:
                    content = "[图片]"
                body["content"] = content
        else:
            # 自动检测 Markdown 语法（仅识别明确的格式标记，避免误判普通文本）
            _MD_PATTERNS = (r'\*\*[^*]+\*\*', r'\*[^*]+\*', r'~~[^~]+~~', r'^> ', r'`[^`]+`', r'\[.+\]\(.+\)', r'^#{1,3} ')
            import re as _re
            is_md = any(_re.search(p, content, _re.MULTILINE) for p in _MD_PATTERNS)
            if is_md:
                body["msg_type"] = 2
                body["markdown"] = {"content": content}
            else:
                body["content"] = content

        if reply_msg_id:
            body["msg_id"] = reply_msg_id

        if keyboard:
            buttons = [b.strip() for b in keyboard.split("|") if b.strip()][:4]
            if buttons:
                body.setdefault("msg_type", 2)
                body["keyboard"] = {
                    "content": {
                        "rows": [{
                            "buttons": [
                                {
                                    "id": f"btn_{i}",
                                    "render_data": {"label": b, "visited_label": b},
                                    "action": {"type": 2, "permission": {"type": 2}, "data": b, "unsupport_tips": "请升级QQ版本"},
                                }
                                for i, b in enumerate(buttons)
                            ]
                        }]
                    }
                }

        try:
            resp = await self._http.post(
                f"{self._API_BASE}/v2/groups/{group_id}/messages",
                json=body,
                headers=self._auth_headers(),
            )
            data = resp.json()
            msg_id = str(data.get("id") or "")
            if msg_id and record_sent:
                self.record_sent_message_id(msg_id)
            return msg_id if msg_id else None
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 发送群消息失败: {e}")
            return None

    async def send_message(self, user_id: str, message: str) -> Optional[str]:
        """发送私聊纯文本（兼容 voice_reply_service）"""
        return await self.send_private_message_segments(
            user_id, [{"type": "text", "data": {"text": message}}],
        )

    async def send_group_message(self, group_id: str, message: str) -> Optional[str]:
        """发送群聊纯文本（兼容旧接口）"""
        return await self.send_group_message_segments(
            group_id, [{"type": "text", "data": {"text": message}}],
        )

    async def send_private_record(self, user_id: str, file_uri: str) -> None:
        """发送私聊语音 — 开放平台不支持，降级为文本"""
        await self.send_private_message_segments(
            user_id, [{"type": "text", "data": {"text": "[语音消息]"}}],
        )

    async def send_private_message_segments(
        self, user_id: str, segments: list[dict[str, Any]]
    ) -> Optional[str]:
        content_parts: list[str] = []
        image_url = ""
        for seg in segments:
            data = seg.get("data") or {}
            if seg.get("type") == "text":
                content_parts.append(str(data.get("text") or ""))
            elif seg.get("type") == "image":
                image_url = str(data.get("file") or "")

        content = "".join(content_parts).strip()
        if not content and not image_url:
            return None
        # 私聊图片需要先上传获取 file_info，当前仅支持文本；纯图片降级为文本占位
        if image_url and not content:
            content = "[图片]"

        await self._ensure_token()
        try:
            resp = await self._http.post(
                f"{self._API_BASE}/v2/users/{user_id}/messages",
                json={"content": content},
                headers=self._auth_headers(),
            )
            data = resp.json()
            return str(data.get("id") or "") or None
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 发送私聊失败: {e}")
            return None

    async def send_group_poke(self, group_id: str, user_id: str) -> bool:
        # QQ 开放平台不支持戳一戳
        await self.send_group_message_segments(
            group_id,
            [{"type": "text", "data": {"text": f" (戳了戳 {user_id})"}}],
            record_sent=False,
        )
        return True

    async def send_group_image(
        self, group_id: str, image_data: str, *, reply_message_id: str = "", at_user_id: str = ""
    ) -> Optional[str]:
        segments: list[dict[str, Any]] = []
        if reply_message_id:
            segments.append({"type": "reply", "data": {"id": reply_message_id}})
        if at_user_id:
            segments.append({"type": "at", "data": {"qq": at_user_id}})
        segments.append({"type": "image", "data": {"file": image_data}})
        return await self.send_group_message_segments(group_id, segments, record_sent=False)

    async def send_group_record(
        self, group_id: str, file_uri: str, *, reply_message_id: str = "", at_user_id: str = ""
    ) -> None:
        segments: list[dict[str, Any]] = []
        if reply_message_id:
            segments.append({"type": "reply", "data": {"id": reply_message_id}})
        if at_user_id:
            segments.append({"type": "at", "data": {"qq": at_user_id}})
        segments.append({"type": "text", "data": {"text": "[语音消息]"}})
        await self.send_group_message_segments(group_id, segments, record_sent=False)

    async def get_login_status(self) -> dict[str, Any]:
        if self._ws and self._self_id:
            return {"status": "online", "self_id": self._self_id, "nickname": self._self_nickname or None}
        return {"status": "offline", "self_id": None, "nickname": None}

    def record_sent_message_id(self, message_id: str) -> None:
        mid = str(message_id or "").strip()
        if mid:
            self._sent_message_ids[mid] = time.time()

    @property
    def onebot_url(self) -> str:
        return self._API_BASE

    @onebot_url.setter
    def onebot_url(self, value: str) -> None:
        pass  # QQ 开放平台不需要外部设置 URL

    async def _try_reconnect(self) -> None:
        """断线重连（指数退避）"""
        delay = 1.0
        while not self._closing:
            try:
                if self.logger:
                    self.logger.info(f"[QQOpenPlatform] 尝试重连 ({delay:.0f}s)...")
                await asyncio.sleep(delay)
                if self._closing:
                    return
                # 清理旧连接
                if self._ws:
                    try: await self._ws.close()
                    except Exception: pass
                self._ws = None; self.ws = None
                # 重新连接 + 握手（优先 Resume 补发遗漏事件）
                await self._refresh_token()
                ws_url = await self._get_gateway_url()
                self._ws = await websockets.connect(ws_url, max_size=2 ** 23)
                self.ws = self._ws
                await self._handshake(is_reconnect=True)
                if self.logger:
                    self.logger.info("[QQOpenPlatform] 重连成功")
                return  # 回到 _receive_loop
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[QQOpenPlatform] 重连失败: {e}")
            delay = min(delay * 2, 60.0)  # 指数退避，上限 60s

    # ==========================================
    # 内部辅助
    # ==========================================

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"QQBot {self._access_token}",
            "Content-Type": "application/json",
        }

    async def _refresh_token(self) -> None:
        try:
            resp = await self._http.post(self._TOKEN_URL, json={
                "appId": self._app_id,
                "clientSecret": self._client_secret,
            })
            data = resp.json()
            self._access_token = str(data.get("access_token") or "")
            expires_in = int(data.get("expires_in") or 7200)
            self._token_expires_at = time.time() + expires_in - 300  # 提前 5 分钟刷新
            if self.logger:
                self.logger.info("[QQOpenPlatform] access_token 已获取")
        except Exception as e:
            if self.logger:
                self.logger.error(f"[QQOpenPlatform] 获取 access_token 失败: {e}")
            raise

    async def _ensure_token(self) -> None:
        if time.time() >= self._token_expires_at:
            await self._refresh_token()

    async def _upload_group_image(self, group_id: str, image_url: str) -> str:
        """上传群聊图片到 QQ 开放平台，返回 file_info 或空串"""
        import os, mimetypes
        image_url = str(image_url or "").strip()
        if not image_url:
            return ""
        # 获取本地文件路径（file:// 或直接路径）
        file_path = image_url
        if file_path.startswith("file://"):
            file_path = file_path[7:]
        if not os.path.isfile(file_path):
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 图片文件不存在: {file_path}")
            return ""
        try:
            mime_type = mimetypes.guess_type(file_path)[0] or "image/png"
            file_size = os.path.getsize(file_path)
            # Step 1: 申请上传
            resp = await self._http.post(
                f"{self._API_BASE}/v2/groups/{group_id}/files",
                json={"file_type": 1, "file_name": os.path.basename(file_path),
                      "file_size": file_size, "mime_type": mime_type},
                headers=self._auth_headers(),
            )
            data = resp.json()
            upload_url = str(data.get("upload_url") or "")
            if not upload_url:
                if self.logger:
                    self.logger.warning(f"[QQOpenPlatform] 申请上传URL失败: {data}")
                return ""
            # Step 2: 上传文件
            with open(file_path, "rb") as f:
                upload_resp = await self._http.put(
                    upload_url,
                    content=f.read(),
                    headers={"Content-Type": mime_type},
                )
            upload_data = upload_resp.json() if upload_resp.text else {}
            file_info = str(upload_data.get("file_info") or data.get("file_info") or "")
            if file_info:
                if self.logger:
                    self.logger.info(f"[QQOpenPlatform] 图片上传成功: {file_info}")
                return file_info
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 图片上传失败: {upload_data}")
            return ""
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 图片上传异常: {e}")
            return ""

    async def _get_gateway_url(self) -> str:
        await self._ensure_token()
        resp = await self._http.get(
            f"{self._API_BASE}/gateway/bot",
            headers=self._auth_headers(),
        )
        data = resp.json()
        return str(data.get("url") or f"{self._API_BASE}/websocket")

    async def _heartbeat_loop(self) -> None:
        while not self._closing:
            if not self._ws:
                await asyncio.sleep(1)
                continue
            await asyncio.sleep(self._heartbeat_interval)
            if self._ws:
                try:
                    await self._ws.send(json.dumps({"op": 1, "d": self._last_seq}))
                except Exception:
                    pass  # _receive_loop 会处理重连

    # ==========================================
    # 事件转换
    # ==========================================

    def _convert_event(self, event_type: str, data: dict[str, Any]) -> Optional[dict[str, Any]]:
        """QQ 开放平台事件 → 内部统一消息格式"""
        author = data.get("author", {})
        user_id = str(author.get("id") or "")
        user_nickname = str(author.get("username") or "") or None

        if event_type == "C2C_MESSAGE_CREATE":
            return {
                "message_type": "private",
                "user_id": user_id,
                "user_nickname": user_nickname,
                "content": str(data.get("content") or ""),
                "message_id": str(data.get("id") or ""),
                "timestamp": int(time.time()),
                "is_at_bot": True,
                "is_reply_to_bot": False,
                "group_id": "",
                "quoted_message_id": "",
                "mentioned_user_ids": [],
                "mentions_other_user": False,
                "mentions_all": False,
                "raw": data,
                "attachments": self._extract_attachments(data),
            }

        if event_type == "GROUP_AT_MESSAGE_CREATE":
            content = str(data.get("content") or "")
            group_id = str(data.get("group_id") or "")
            mentioned_ids: list[str] = []
            mentions_all = False
            # 检查 @ 目标（content 中 <@!id> 格式）
            import re
            for m in re.finditer(r"<@!(\d+)>", content):
                mentioned_ids.append(m.group(1))
            # 去掉 <@!id> 占位符后的纯文本
            clean_content = re.sub(r"<@!\d+>", "", content).strip()

            return {
                "message_type": "group",
                "user_id": user_id,
                "user_nickname": user_nickname,
                "content": clean_content,
                "message_id": str(data.get("id") or ""),
                "timestamp": int(time.time()),
                "is_at_bot": True,
                "is_reply_to_bot": False,
                "group_id": group_id,
                "quoted_message_id": "",  # 暂不支持引用回复检测
                "mentioned_user_ids": mentioned_ids,
                "mentions_other_user": len(mentioned_ids) > 1,
                "mentions_all": mentions_all,
                "raw": data,
                "attachments": self._extract_attachments(data),
            }

        return None

    @staticmethod
    def _extract_attachments(data: dict[str, Any]) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        for att in data.get("attachments") or []:
            if isinstance(att, dict):
                url = att.get("url") or ""
                content_type = str(att.get("content_type") or "")
                if url:
                    att_type = "image" if content_type.startswith("image/") else "file"
                    attachments.append({"type": att_type, "url": url})
        return attachments
