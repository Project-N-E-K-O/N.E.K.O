"""
B站私信客户端封装（基于 bilibili_api）

使用 bilibili_api.session.Session 监听私信事件，
通过 send_msg 发送文本、图片、表情等消息。
"""

import asyncio
import base64
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

import httpx
from bilibili_api import Credential
from bilibili_api.session import Session, EventType, Event
from bilibili_api.user import User as BiliUser
from bilibili_api.video import Video as BiliVideo


class BiliDMClient:
    """B站私信客户端"""

    def __init__(
        self,
        sesdata: str,
        bili_jct: str = "",
        buvid3: str = "",
        dedeuserid: str = "",
        ac_time_value: str = "",
        enable_comment_notifications: bool = True,
        notification_poll_interval_seconds: int = 20,
        notification_max_items: int = 20,
        logger=None,
    ):
        self.logger = logger

        self._credential = Credential(
            sessdata=sesdata,
            bili_jct=bili_jct,
            buvid3=buvid3,
            dedeuserid=dedeuserid,
            ac_time_value=ac_time_value,
        )

        self._session: Optional[Session] = None
        self._session_task: Optional[asyncio.Task] = None
        self._notification_task: Optional[asyncio.Task] = None
        self._running = False
        self._enable_comment_notifications = enable_comment_notifications
        self._notification_poll_interval_seconds = notification_poll_interval_seconds
        self._notification_max_items = notification_max_items
        self._notification_bootstrap_done = False
        self._notification_seen: list[str] = []
        self._notification_seen_set: set[str] = set()
        self._video_info_cache: Dict[int, Dict[str, str]] = {}
        self._user_info_cache: Dict[int, Dict[str, Any]] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    @property
    def is_running(self) -> bool:
        return self._running

    async def connect(self):
        """启动 B站私信监听"""
        if self._running:
            return

        if not self._credential.sessdata or not self._credential.bili_jct:
            raise RuntimeError("B站 Cookie（SESSDATA 和 bili_jct）未完整配置，请在插件前端面板中填写")

        try:
            self._session = Session(self._credential, debug=False)

            @self._session.on(EventType.TEXT)
            async def on_text(event: Event):
                await self._enqueue_event(event, "text")

            @self._session.on(EventType.PICTURE)
            async def on_picture(event: Event):
                await self._enqueue_event(event, "picture")

            @self._session.on(EventType.SHARE_VIDEO)
            async def on_share_video(event: Event):
                await self._enqueue_event(event, "share_video")

            self._running = True
            if self.logger:
                self.logger.info("B站私信监听已启动")

            # Session.start() 是阻塞式轮询，需要在后台任务中运行
            self._session_task = asyncio.create_task(self._run_session())
            if self._enable_comment_notifications:
                self._notification_task = asyncio.create_task(
                    self._run_notification_loop()
                )

        except Exception as e:
            self._running = False
            if self.logger:
                self.logger.error(f"启动 B站私信监听失败: {e}")
            raise

    async def _run_session(self):
        """在后台运行 Session 轮询"""
        try:
            await self._session.start(exclude_self=True)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._running = False
            if self.logger:
                self.logger.error(f"B站 Session 轮询异常退出: {e}")

    async def disconnect(self):
        """停止 B站私信监听"""
        self._running = False
        if self._notification_task:
            self._notification_task.cancel()
            try:
                await self._notification_task
            except asyncio.CancelledError:
                pass
            self._notification_task = None
        if self._session:
            try:
                self._session.close()
                if self.logger:
                    self.logger.info("B站私信监听已停止")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"停止 B站私信监听失败: {e}")

    def _cookies(self) -> Dict[str, str]:
        cookies: Dict[str, str] = {}
        if self._credential.sessdata:
            cookies["SESSDATA"] = self._credential.sessdata
        if self._credential.bili_jct:
            cookies["bili_jct"] = self._credential.bili_jct
        if self._credential.buvid3:
            cookies["buvid3"] = self._credential.buvid3
        if self._credential.dedeuserid:
            cookies["DedeUserID"] = self._credential.dedeuserid
        return cookies

    @staticmethod
    def _notification_content(item: Dict[str, Any]) -> str:
        details = item.get("item") or {}
        if not isinstance(details, dict):
            return ""
        return str(
            details.get("source_content")
            or details.get("target_reply_content")
            or details.get("root_reply_content")
            or details.get("title")
            or ""
        ).strip()

    @staticmethod
    def _comment_reply_target(item: Dict[str, Any]) -> Optional[Dict[str, int]]:
        details = item.get("item") or {}
        if not isinstance(details, dict):
            return None
        try:
            comment_type = int(details.get("business_id"))
            oid = int(details.get("subject_id"))
        except (TypeError, ValueError):
            return None

        def first_id(*values: Any) -> Optional[int]:
            for value in values:
                if value not in (None, "", 0):
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        continue
            return None

        root = first_id(
            details.get("root_id"), details.get("source_id"), details.get("target_id")
        )
        parent = first_id(
            details.get("source_id"), details.get("target_id"), details.get("root_id")
        )
        target = {"type": comment_type, "oid": oid}
        if root is not None:
            target["root"] = root
        if parent is not None:
            target["parent"] = parent
        return target

    @staticmethod
    def _video_aid_from_notification(item: Dict[str, Any]) -> Optional[int]:
        """Return the AID for a video-comment notification, if applicable."""
        details = item.get("item") or {}
        if not isinstance(details, dict):
            return None
        try:
            if int(details.get("business_id")) != 1:
                return None
            return int(details.get("subject_id"))
        except (TypeError, ValueError):
            return None

    async def get_video_context(self, aid: int) -> Optional[Dict[str, str]]:
        """Fetch compact video metadata suitable for an AI reply context."""
        if aid <= 0:
            return None
        cached = self._video_info_cache.get(aid)
        if cached is not None:
            return dict(cached)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://api.bilibili.com/x/web-interface/view",
                    params={"aid": aid},
                    cookies=self._cookies(),
                    headers={
                        "Referer": "https://www.bilibili.com/",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                    },
                )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict) or payload.get("code") != 0:
                return None
            bvid = str(data.get("bvid") or "").strip()
            owner = data.get("owner") or {}
            context = {
                "aid": str(aid),
                "title": str(data.get("title") or "").strip(),
                "description": str(data.get("desc") or "").strip(),
                "bvid": bvid,
                "url": (
                    f"https://www.bilibili.com/video/{bvid}"
                    if bvid
                    else f"https://www.bilibili.com/video/av{aid}"
                ),
                "owner_name": str(owner.get("name") or "").strip()
                if isinstance(owner, dict)
                else "",
            }
            self._video_info_cache[aid] = context
            return dict(context)
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"获取 B站视频信息失败 aid={aid}: {exc}")
            return None

    def _mark_notification_seen(self, source: str, item: Dict[str, Any]) -> bool:
        notification_id = str(item.get("id") or "").strip()
        if not notification_id:
            return False
        key = f"{source}:{notification_id}"
        if key in self._notification_seen_set:
            return False
        self._notification_seen.append(key)
        self._notification_seen_set.add(key)
        if len(self._notification_seen) > 500:
            self._notification_seen_set.discard(self._notification_seen.pop(0))
        return True

    def _is_at_current_user(self, item: Dict[str, Any]) -> bool:
        current_uid = str(self._credential.dedeuserid or "").strip()
        if not current_uid:
            return False
        details = item.get("item") or {}
        at_details = details.get("at_details") if isinstance(details, dict) else None
        return isinstance(at_details, list) and any(
            isinstance(detail, dict) and str(detail.get("mid") or "") == current_uid
            for detail in at_details
        )

    async def _run_notification_loop(self) -> None:
        """Poll B站消息中心的评论回复与 @ 通知。"""
        while self._running:
            try:
                await self._poll_comment_notifications()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.logger:
                    self.logger.warning(f"获取 B站评论通知失败: {exc}")
            await asyncio.sleep(self._notification_poll_interval_seconds)

    async def _poll_comment_notifications(self) -> None:
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                reply_items = await self._get_notification_items(
                    client, "https://api.bilibili.com/x/msgfeed/reply"
                )
            except Exception as exc:
                reply_items = []
                if self.logger:
                    self.logger.warning(f"获取 B站回复通知失败: {exc}")
            try:
                at_items = await self._get_notification_items(
                    client, "https://api.bilibili.com/x/msgfeed/at"
                )
            except Exception as exc:
                at_items = []
                try:
                    at_items = await self._get_notification_items(
                        client, "https://api.vc.bilibili.com/x/im/web/msgfeed/at"
                    )
                except Exception:
                    if self.logger:
                        self.logger.warning(f"获取 B站 @ 通知失败: {exc}")

        if not self._notification_bootstrap_done:
            for item in reply_items:
                self._mark_notification_seen("reply", item)
            for item in at_items:
                self._mark_notification_seen("at", item)
            self._notification_bootstrap_done = True
            return

        for source, items in (("reply", reply_items), ("at", at_items)):
            for item in items:
                if not self._mark_notification_seen(source, item):
                    continue
                if source == "at" and not self._is_at_current_user(item):
                    continue
                await self._enqueue_comment_notification(item, source)

    async def _get_notification_items(
        self, client: httpx.AsyncClient, url: str
    ) -> List[Dict[str, Any]]:
        response = await client.get(
            url,
            params={"build": 0, "mobi_app": "web"},
            cookies=self._cookies(),
            headers={
                "Referer": "https://message.bilibili.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("B站通知接口返回了无效数据")
        if payload.get("code") not in (None, 0):
            raise RuntimeError(
                f"B站通知接口错误: {payload.get('message') or payload.get('msg') or payload.get('code')}"
            )
        items = (payload.get("data") or {}).get("items") or []
        return [item for item in items if isinstance(item, dict)][
            : self._notification_max_items
        ]

    async def _enqueue_comment_notification(
        self, item: Dict[str, Any], source: str
    ) -> None:
        user = item.get("user") or {}
        if not isinstance(user, dict):
            return
        sender_uid = str(user.get("mid") or "").strip()
        content = self._notification_content(item)
        reply_target = self._comment_reply_target(item)
        if not sender_uid or not content or not reply_target:
            return
        if sender_uid == str(self._credential.dedeuserid or "").strip():
            return
        message = {
            "sender_uid": sender_uid,
            "sender_nickname": str(user.get("nickname") or sender_uid),
            "msg_kind": "comment_at" if source == "at" else "comment_reply",
            "notification_source": source,
            "msg_key": str(item.get("id") or ""),
            "timestamp": int(item.get("reply_time") or time.time()),
            "content": content,
            "content_type": "text",
            "reply_target": reply_target,
            "video_aid": self._video_aid_from_notification(item),
            "conversation_key": f"comment:{reply_target['type']}:{reply_target['oid']}",
            "raw_event": item,
        }
        try:
            self._message_queue.put_nowait(message)
        except asyncio.QueueFull:
            _ = self._message_queue.get_nowait()
            self._message_queue.put_nowait(message)

    async def send_comment_reply(self, target: Dict[str, int], text: str) -> None:
        """回复消息中心通知所对应的 B站评论线程。"""
        payload: Dict[str, Any] = {
            "type": target["type"],
            "oid": target["oid"],
            "message": text,
            "plat": 1,
            "csrf": self._credential.bili_jct,
        }
        for key in ("root", "parent"):
            if target.get(key):
                payload[key] = target[key]
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                "https://api.bilibili.com/x/v2/reply/add",
                data=payload,
                cookies=self._cookies(),
                headers={
                    "Referer": "https://www.bilibili.com/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                },
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("code") != 0:
            message = result.get("message") if isinstance(result, dict) else "未知错误"
            raise RuntimeError(f"B站评论回复失败: {message}")

    async def _enqueue_event(self, event: Event, msg_kind: str):
        """将原始事件标准化后放入队列"""
        try:
            sender_uid = str(event.sender_uid)

            # 获取用户昵称
            nickname = await self._get_user_nickname(event.sender_uid)

            # 构建标准化消息
            message = {
                "sender_uid": sender_uid,
                "sender_nickname": nickname,
                "msg_kind": msg_kind,
                "msg_key": str(event.msg_key),
                "timestamp": int(event.timestamp) if event.timestamp else int(time.time()),
                "raw_event": event,
            }

            # 根据消息类型提取内容
            if msg_kind == "text":
                message["content"] = str(event.content) if event.content else ""
                message["content_type"] = "text"

            elif msg_kind == "picture":
                content = event.content
                if hasattr(content, "url") and content.url:
                    message["content"] = content.url
                    message["content_type"] = "image_url"
                else:
                    message["content"] = "[图片]"
                    message["content_type"] = "text"

            elif msg_kind == "share_video":
                content = event.content
                if isinstance(content, BiliVideo):
                    try:
                        video_info = await content.get_info()
                        title = video_info.get("title", "未知")
                        bvid = video_info.get("bvid", "")
                        owner_name = video_info.get("owner", {}).get("name", "未知")
                        view = video_info.get("stat", {}).get("view", 0)
                        like = video_info.get("stat", {}).get("like", 0)
                        url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
                        message["content"] = (
                            f"[分享视频] {title}\nUP主: {owner_name} | 播放: {view} | 点赞: {like}\n{url}"
                        )
                    except Exception as e:
                        bvid = getattr(content, "bvid", "")
                        message["content"] = (
                            f"[分享视频] https://www.bilibili.com/video/{bvid}"
                            if bvid else "[分享视频]"
                        )
                elif hasattr(content, "bvid") and content.bvid:
                    message["content"] = f"[分享视频] https://www.bilibili.com/video/{content.bvid}"
                else:
                    message["content"] = "[分享视频]"
                message["content_type"] = "text"

            # 放入队列
            try:
                self._message_queue.put_nowait(message)
            except asyncio.QueueFull:
                # 队列满时丢弃最旧的消息
                _ = self._message_queue.get_nowait()
                self._message_queue.put_nowait(message)

            if self.logger:
                self.logger.info(f"收到 B站私信 [{msg_kind}] from {sender_uid} ({nickname})")

        except Exception as e:
            if self.logger:
                self.logger.error(f"处理 B站私信事件失败: {e}")

    async def receive_message(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """接收一条标准化消息"""
        try:
            return await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def _get_user_nickname(self, uid: int) -> str:
        """获取 B站用户昵称（带内存缓存）"""
        uid_int = int(uid)
        if uid_int in self._user_info_cache:
            return self._user_info_cache[uid_int].get("name", str(uid))

        try:
            bili_user = BiliUser(uid=uid_int, credential=self._credential)
            info = await bili_user.get_user_info()
            nickname = info.get("name", str(uid))
            self._user_info_cache[uid_int] = info
            return nickname
        except Exception as e:
            if self.logger:
                self.logger.warning(f"获取用户 {uid} 昵称失败: {e}")
            return str(uid)

    async def download_image_as_base64(self, url: str) -> Optional[str]:
        """下载 B站图片并转为 base64 data URL（需 Cookie 鉴权）"""
        cookies = {}
        if self._credential.sessdata:
            cookies["SESSDATA"] = self._credential.sessdata
        if self._credential.bili_jct:
            cookies["bili_jct"] = self._credential.bili_jct
        if self._credential.buvid3:
            cookies["buvid3"] = self._credential.buvid3
        if self._credential.dedeuserid:
            cookies["DedeUserID"] = self._credential.dedeuserid

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                resp = await client.get(
                    url,
                    cookies=cookies,
                    headers={
                        "Referer": "https://www.bilibili.com",
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/116.0.0.0 Safari/537.36"
                        ),
                    },
                )
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "image/png")
                b64_str = base64.b64encode(resp.content).decode("utf-8")
                return f"data:{content_type};base64,{b64_str}"
        except Exception as e:
            if self.logger:
                self.logger.error(f"下载图片失败 {url}: {e}")
            return None

    async def send_text(self, user_id: str, text: str):
        """发送文本私信"""
        from bilibili_api.session import send_msg
        from bilibili_api.session import EventType as SessionEventType

        await send_msg(self._credential, int(user_id), SessionEventType.TEXT, text)
        if self.logger:
            self.logger.info(f"已发送文本私信给 {user_id}")

    async def send_image(self, user_id: str, image_source: str):
        """发送图片私信，支持 URL 和 base64 两种来源"""
        from bilibili_api.session import send_msg
        from bilibili_api.session import EventType as SessionEventType
        from bilibili_api.utils.picture import Picture

        if image_source.startswith("data:"):
            # base64 data URL
            # 提取 base64 部分
            _, b64_data = image_source.split(",", 1)
            img_bytes = base64.b64decode(b64_data)
            pic = Picture.from_content(img_bytes, "png")
        elif image_source.startswith(("http://", "https://")):
            # URL
            pic = await Picture.load_url(image_source)
        else:
            # 假设是 base64 字符串
            img_bytes = base64.b64decode(image_source)
            pic = Picture.from_content(img_bytes, "png")

        await send_msg(self._credential, int(user_id), SessionEventType.PICTURE, pic)
        if self.logger:
            self.logger.info(f"已发送图片私信给 {user_id}")

    async def send_emoji(self, user_id: str, emoji_text: str):
        """发送表情私信（以文本形式发送 emoji 字符）"""
        await self.send_text(user_id, emoji_text)
