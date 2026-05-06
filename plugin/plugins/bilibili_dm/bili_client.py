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
        self._running = False
        self._user_info_cache: Dict[int, Dict[str, Any]] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    @property
    def is_running(self) -> bool:
        return self._running

    async def connect(self):
        """启动 B站私信监听"""
        if self._running:
            return

        if not self._credential.sessdata:
            raise RuntimeError("B站 Cookie (SESSDATA) 未配置，请在 plugin.toml 中填写")

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
            asyncio.create_task(self._run_session())

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
        if self._session:
            try:
                self._session.close()
                self._running = False
                if self.logger:
                    self.logger.info("B站私信监听已停止")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"停止 B站私信监听失败: {e}")

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
