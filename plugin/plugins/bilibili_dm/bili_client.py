"""
B站集成客户端封装（基于 bilibili_api）

使用 bilibili_api.session.Session 监听私信事件，
并轮询评论回复与 @ 通知。
"""

import asyncio
import base64
import json
import os
import time
from collections import OrderedDict, deque
from typing import Any, Callable, Dict, List, Optional

import httpx
from bilibili_api import Credential
from bilibili_api.session import Session, EventType, Event
from bilibili_api.user import User as BiliUser, get_self_info
from bilibili_api.video import Video as BiliVideo


class BiliDMClient:
    """B站私信与评论通知客户端。"""

    NOTIFICATION_BACKLOG_LIMIT = 500
    NOTIFICATION_MAX_RETRY_ATTEMPTS = 5
    NOTIFICATION_DEFER_DELAY_SECONDS = 0.5
    SESSION_RESTART_INITIAL_DELAY_SECONDS = 2
    SESSION_RESTART_MAX_DELAY_SECONDS = 60
    VIDEO_CONTEXT_CACHE_LIMIT = 200
    BILIBILI_ACCEPT_ENCODING = "gzip, deflate"

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
        self._notification_bootstrap_done = {"reply": False, "at": False}
        self._notification_seen: list[str] = []
        self._notification_seen_set: set[str] = set()
        self._notification_inflight: set[str] = set()
        self._notification_retries: dict[str, Dict[str, Any]] = {}
        self._notification_blocked_conversations: dict[str, str] = {}
        self._notification_feed_seen = {"reply": [], "at": []}
        self._notification_feed_seen_set = {"reply": set(), "at": set()}
        self._notification_pending: deque[tuple[str, Dict[str, Any]]] = deque()
        self._notification_backlog_limit = self.NOTIFICATION_BACKLOG_LIMIT
        self._current_uid = str(dedeuserid or "").strip()
        self._video_info_cache: OrderedDict[int, Dict[str, str]] = OrderedDict()
        self._video_info_cache_limit = self.VIDEO_CONTEXT_CACHE_LIMIT
        self._user_info_cache: Dict[int, Dict[str, Any]] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    @property
    def is_running(self) -> bool:
        return self._running

    async def connect(self):
        """启动 B站私信与评论通知监听。"""
        if self._running:
            return

        if not self._credential.sessdata or not self._credential.bili_jct:
            raise RuntimeError(
                "B站 Cookie（SESSDATA 和 bili_jct）未完整配置，请在插件前端面板中填写"
            )

        try:
            self._disable_brotli_for_bilibili_api()
            # DedeUserID 是可选提示，不能当作当前 SESSDATA 所属账号的权威值。
            # 每次连接都向 B站重新确认，避免替换 Cookie 后沿用旧账号 UID。
            await self._resolve_current_uid(force=True)

            self._session = self._create_session()

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

    def _create_session(self) -> Session:
        """Create a fresh private-message session with all event handlers."""
        session = Session(self._credential, debug=False)

        @session.on(EventType.TEXT)
        async def on_text(event: Event):
            await self._enqueue_event(event, "text")

        @session.on(EventType.PICTURE)
        async def on_picture(event: Event):
            await self._enqueue_event(event, "picture")

        @session.on(EventType.SHARE_VIDEO)
        async def on_share_video(event: Event):
            await self._enqueue_event(event, "share_video")

        return session

    async def _run_session(self):
        """Supervise private-message polling and reconnect after failures."""
        retry_delay = self.SESSION_RESTART_INITIAL_DELAY_SECONDS
        while self._running:
            try:
                await self._session.start(exclude_self=True)
                if self._running and self.logger:
                    self.logger.warning("B站私信 Session 意外停止，准备重新连接")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.logger:
                    self.logger.error(
                        "B站私信 Session 轮询异常，准备重新连接；"
                        f"评论通知继续运行: {exc}"
                    )

            if not self._running:
                break
            await asyncio.sleep(retry_delay)
            if not self._running:
                break
            self._session = self._create_session()
            retry_delay = min(retry_delay * 2, self.SESSION_RESTART_MAX_DELAY_SECONDS)

    async def disconnect(self):
        """停止 B站私信与评论通知监听。"""
        self._running = False
        if self._session:
            try:
                self._session.close()
                if self.logger:
                    self.logger.info("B站私信监听已停止")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"停止 B站私信监听失败: {e}")
            self._session = None

        tasks = [
            task
            for task in (self._session_task, self._notification_task)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._session_task = None
        self._notification_task = None

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

    def _disable_brotli_for_bilibili_api(self) -> None:
        """Prevent bilibili-api's shared transport from negotiating Brotli."""
        try:
            from bilibili_api.utils.network import get_client

            session = get_client().get_wrapped_session()
            session.headers["Accept-Encoding"] = self.BILIBILI_ACCEPT_ENCODING
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"设置 B站请求 Accept-Encoding 失败: {exc}")

    async def _resolve_current_uid(self, *, force: bool = False) -> bool:
        """解析当前登录账号 UID；通知处理依赖它来排除账号自身。"""
        if self._current_uid and not force:
            return True
        if force:
            self._current_uid = ""
            self._credential.dedeuserid = ""
        try:
            self_info = await get_self_info(self._credential)
            self._current_uid = str(self_info.get("mid") or "").strip()
            if self._current_uid:
                self._credential.dedeuserid = self._current_uid
                return True
        except Exception as exc:
            if self.logger:
                self.logger.warning(
                    f"无法解析当前 B站账号 UID，评论通知将在下轮重试: {exc}"
                )
        return False

    @staticmethod
    def _notification_content(item: Dict[str, Any]) -> str:
        details = item.get("item") or {}
        if not isinstance(details, dict):
            return ""
        source = str(
            details.get("source_content") or details.get("title") or ""
        ).strip()
        target = str(details.get("target_reply_content") or "").strip()
        root = str(details.get("root_reply_content") or "").strip()
        if not source:
            source = target or root
        if not source:
            return ""

        parts = [source]
        if target and target != source:
            parts.append(f"[被回复评论] {target}")
        if root and root not in (source, target):
            parts.append(f"[根评论] {root}")
        return "\n".join(parts)

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
            self._video_info_cache.move_to_end(aid)
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
                        "Accept-Encoding": self.BILIBILI_ACCEPT_ENCODING,
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
            self._cache_video_context(aid, context)
            return dict(context)
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"获取 B站视频信息失败 aid={aid}: {exc}")
            return None

    def _cache_video_context(self, aid: int, context: Dict[str, str]) -> None:
        """Keep video contexts bounded while retaining recently used entries."""
        self._video_info_cache[aid] = context
        self._video_info_cache.move_to_end(aid)
        cache_limit = max(
            1,
            int(
                getattr(
                    self, "_video_info_cache_limit", self.VIDEO_CONTEXT_CACHE_LIMIT
                )
            ),
        )
        while len(self._video_info_cache) > cache_limit:
            self._video_info_cache.popitem(last=False)

    @staticmethod
    def _notification_identity(item: Dict[str, Any]) -> str:
        """返回跨 reply/@ 消息流稳定的底层评论身份。"""
        details = item.get("item") or {}
        if isinstance(details, dict):
            business_id = str(details.get("business_id") or "").strip()
            subject_id = str(details.get("subject_id") or "").strip()
            source_id = str(details.get("source_id") or "").strip()
            if business_id and subject_id and source_id:
                return f"comment:{business_id}:{subject_id}:{source_id}"

        notification_id = str(item.get("id") or "").strip()
        return f"notification:{notification_id}" if notification_id else ""

    @classmethod
    def _notification_feed_identity(cls, item: Dict[str, Any]) -> str:
        """返回单一消息流内稳定的事件身份，用作分页停止边界。"""
        notification_id = str(item.get("id") or "").strip()
        return notification_id or cls._notification_identity(item)

    def _mark_notification_seen(self, source: str, item: Dict[str, Any]) -> bool:
        """Claim a notification while keeping feed and delivery state separate."""
        self._ensure_notification_delivery_state()
        feed_key = self._notification_feed_identity(item)
        if feed_key and feed_key not in self._notification_feed_seen_set[source]:
            self._notification_feed_seen[source].append(feed_key)
            self._notification_feed_seen_set[source].add(feed_key)
            if len(self._notification_feed_seen[source]) > 500:
                expired = self._notification_feed_seen[source].pop(0)
                self._notification_feed_seen_set[source].discard(expired)

        # reply 与 @ 流中的同一底层评论必须共享业务去重身份。只有发送成功
        # 或明确无需回复后才进入 seen；处理中和等待重试的项目留在 inflight。
        key = self._notification_identity(item)
        if not key:
            return False
        if key in self._notification_seen_set or key in self._notification_inflight:
            return False
        self._notification_inflight.add(key)
        return True

    def _ensure_notification_delivery_state(self) -> None:
        # 兼容测试和热重载时由旧版本构造的客户端实例。
        if not hasattr(self, "_notification_inflight"):
            self._notification_inflight = set()
        if not hasattr(self, "_notification_retries"):
            self._notification_retries = {}
        if not hasattr(self, "_notification_blocked_conversations"):
            self._notification_blocked_conversations = {}

    def complete_comment_notification(self, identity: str) -> None:
        """Mark a claimed notification terminal after delivery or intentional skip."""
        self._ensure_notification_delivery_state()
        key = str(identity or "").strip()
        if not key:
            return
        self._notification_inflight.discard(key)
        self._notification_retries.pop(key, None)
        for conversation_key, blocked_identity in list(
            self._notification_blocked_conversations.items()
        ):
            if blocked_identity == key:
                self._notification_blocked_conversations.pop(conversation_key, None)
        if key in self._notification_seen_set:
            return
        self._notification_seen.append(key)
        self._notification_seen_set.add(key)
        if len(self._notification_seen) > 500:
            self._notification_seen_set.discard(self._notification_seen.pop(0))

    def retry_comment_notification(self, message: Dict[str, Any]) -> None:
        """Schedule a failed notification with capped exponential backoff."""
        self._ensure_notification_delivery_state()
        identity = str(message.get("notification_identity") or "").strip()
        if not identity or identity not in self._notification_inflight:
            return
        retry_message = dict(message)
        attempt = int(retry_message.get("notification_attempt") or 0) + 1
        retry_message["notification_attempt"] = attempt
        if attempt > self.NOTIFICATION_MAX_RETRY_ATTEMPTS:
            self.complete_comment_notification(identity)
            if self.logger:
                self.logger.error(
                    "B站评论通知重试达到上限，已转为终止状态: "
                    f"identity={identity}, attempts={attempt - 1}"
                )
            return

        # retry 与 pending 使用相同容量级别；永久故障时将最旧重试转为终止
        # 状态，避免 retry/inflight 随新通知持续增长。
        self._notification_retries.pop(identity, None)
        if len(self._notification_retries) >= self._notification_backlog_limit:
            expired_identity = next(iter(self._notification_retries))
            self.complete_comment_notification(expired_identity)
            if self.logger:
                self.logger.error(
                    "B站评论通知重试队列达到上限，已终止最旧重试: "
                    f"identity={expired_identity}, "
                    f"limit={self._notification_backlog_limit}"
                )
        delay = min(5 * (2 ** min(attempt - 1, 6)), 300)
        conversation_key = str(retry_message.get("conversation_key") or "").strip()
        if conversation_key:
            self._notification_blocked_conversations[conversation_key] = identity
        self._notification_retries[identity] = {
            "message": retry_message,
            "ready_at": time.monotonic() + delay,
        }
        if self.logger:
            self.logger.warning(
                "B站评论通知将在稍后重试: "
                f"identity={identity}, attempt={attempt}, delay={delay}s"
            )

    def defer_comment_notification_behind_retry(self, message: Dict[str, Any]) -> bool:
        """Delay a newer thread item until its failed predecessor is terminal."""
        self._ensure_notification_delivery_state()
        identity = str(message.get("notification_identity") or "").strip()
        conversation_key = str(message.get("conversation_key") or "").strip()
        if not identity or not conversation_key:
            return False
        blocked_identity = self._notification_blocked_conversations.get(
            conversation_key
        )
        if not blocked_identity or blocked_identity == identity:
            return False

        retry_message = dict(message)
        now = time.monotonic()
        blocked_retry = self._notification_retries.get(blocked_identity) or {}
        blocked_ready_at = float(blocked_retry.get("ready_at") or now)
        ready_at = max(
            now + self.NOTIFICATION_DEFER_DELAY_SECONDS,
            blocked_ready_at + 0.001,
        )
        self._notification_retries.pop(identity, None)
        if len(self._notification_retries) >= self._notification_backlog_limit:
            expired_identity = next(iter(self._notification_retries))
            self.complete_comment_notification(expired_identity)
        self._notification_retries[identity] = {
            "message": retry_message,
            "ready_at": ready_at,
        }
        return True

    def _drain_notification_retries(self) -> int:
        """Move due retries back to the consumer queue without duplicating claims."""
        self._ensure_notification_delivery_state()
        now = time.monotonic()
        ready = sorted(
            (
                (identity, retry)
                for identity, retry in self._notification_retries.items()
                if float(retry.get("ready_at") or 0) <= now
            ),
            key=lambda entry: float(entry[1].get("ready_at") or 0),
        )
        enqueued = 0
        for identity, retry in ready:
            try:
                self._message_queue.put_nowait(retry["message"])
            except asyncio.QueueFull:
                break
            self._notification_retries.pop(identity, None)
            enqueued += 1
        return enqueued

    def _is_at_current_user(self, item: Dict[str, Any]) -> bool:
        current_uid = self._current_uid
        if not current_uid:
            return False
        details = item.get("item") or {}
        at_details = details.get("at_details") if isinstance(details, dict) else None
        if not isinstance(at_details, list) or not at_details:
            return True
        return any(
            isinstance(detail, dict) and str(detail.get("mid") or "") == current_uid
            for detail in at_details
        )

    async def _run_notification_loop(self) -> None:
        """Poll B站消息中心的评论回复与 @ 通知。"""
        while self._running:
            try:
                if await self._resolve_current_uid():
                    await self._poll_comment_notifications()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.logger:
                    self.logger.warning(f"获取 B站评论通知失败: {exc}")
            await asyncio.sleep(self._notification_poll_interval_seconds)

    async def _poll_comment_notifications(self) -> None:
        self._drain_notification_retries()
        reply_succeeded = False
        at_succeeded = False
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                reply_items = await self._get_notification_items(
                    client,
                    "https://api.bilibili.com/x/msgfeed/reply",
                    paginate=self._notification_bootstrap_done["reply"],
                )
                reply_succeeded = True
            except Exception as exc:
                reply_items = []
                if self.logger:
                    self.logger.warning(f"获取 B站回复通知失败: {exc}")
            try:
                at_items = await self._get_notification_items(
                    client,
                    "https://api.bilibili.com/x/msgfeed/at",
                    paginate=self._notification_bootstrap_done["at"],
                )
                at_succeeded = True
            except Exception as exc:
                at_items = []
                try:
                    at_items = await self._get_notification_items(
                        client,
                        "https://api.vc.bilibili.com/x/im/web/msgfeed/at",
                        paginate=self._notification_bootstrap_done["at"],
                    )
                    at_succeeded = True
                except Exception as fallback_exc:
                    if self.logger:
                        self.logger.warning(
                            f"获取 B站 @ 通知失败: {exc}; 备用接口失败: {fallback_exc}"
                        )

        dropped_from_pending = 0
        new_notifications: list[tuple[int, int, str, Dict[str, Any]]] = []
        sequence = 0
        for source, items, succeeded in (
            ("reply", reply_items, reply_succeeded),
            ("at", at_items, at_succeeded),
        ):
            if not succeeded:
                continue
            if not self._notification_bootstrap_done[source]:
                for item in items:
                    if self._mark_notification_seen(source, item):
                        self.complete_comment_notification(
                            self._notification_identity(item)
                        )
                self._notification_bootstrap_done[source] = True
                continue

            # 单个消息源通常按新到旧返回；反转后，同时间或缺少时间字段时
            # 仍保持旧到新的处理顺序。
            for item in reversed(items):
                if not self._mark_notification_seen(source, item):
                    continue
                if source == "at" and not self._is_at_current_user(item):
                    self.complete_comment_notification(
                        self._notification_identity(item)
                    )
                    continue
                event_time = self._notification_event_time(source, item)
                new_notifications.append((event_time, sequence, source, item))
                sequence += 1

        # 两个消息源各自按新到旧返回。合并后按事件时间全局排序，避免较晚
        # 拉取的 @ 通知被固定排在更晚发生的回复之后。
        for _, _, source, item in sorted(new_notifications):
            if len(self._notification_pending) >= self._notification_backlog_limit:
                _, dropped_item = self._notification_pending.popleft()
                self.complete_comment_notification(
                    self._notification_identity(dropped_item)
                )
                dropped_from_pending += 1
            self._notification_pending.append((source, item))

        if dropped_from_pending and self.logger:
            self.logger.warning(
                "B站通知积压达到上限，已丢弃最旧待处理项: "
                f"dropped={dropped_from_pending}, "
                f"limit={self._notification_backlog_limit}"
            )

        processed = 0
        while self._notification_pending and processed < self._notification_max_items:
            source, item = self._notification_pending.popleft()
            enqueued = await self._enqueue_comment_notification(item, source)
            if not enqueued:
                self.complete_comment_notification(self._notification_identity(item))
            processed += 1

    @staticmethod
    def _notification_event_time(source: str, item: Dict[str, Any]) -> int:
        """Return a comparable event timestamp for reply and @ feed entries."""
        preferred_key = "reply_time" if source == "reply" else "at_time"
        for key in (preferred_key, "reply_time", "at_time", "timestamp", "time"):
            try:
                value = int(item.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value:
                return value
        return 0

    async def _get_notification_items(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        paginate: bool = False,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"build": 0, "mobi_app": "web"}
        source = "reply" if url.rstrip("/").endswith("/reply") else "at"
        time_param = "reply_time" if source == "reply" else "at_time"
        retained: List[Dict[str, Any]] = []
        skipped_for_capacity = 0
        visited_cursors: set[tuple[str, str]] = set()

        while True:
            response = await client.get(
                url,
                params=params,
                cookies=self._cookies(),
                headers={
                    "Referer": "https://message.bilibili.com/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                    "Accept-Encoding": self.BILIBILI_ACCEPT_ENCODING,
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

            data = payload.get("data") or {}
            if not isinstance(data, dict):
                raise RuntimeError("B站通知接口 data 字段无效")
            page_items = [
                item for item in (data.get("items") or []) if isinstance(item, dict)
            ]
            reached_seen = False
            for item in page_items:
                feed_identity = self._notification_feed_identity(item)
                if (
                    paginate
                    and feed_identity
                    and feed_identity in self._notification_feed_seen_set[source]
                ):
                    reached_seen = True
                    break
                if len(retained) < self._notification_backlog_limit:
                    retained.append(item)
                else:
                    skipped_for_capacity += 1

            capacity_reached = len(retained) >= self._notification_backlog_limit
            if not paginate or reached_seen or capacity_reached:
                break
            cursor = data.get("cursor") or {}
            if not isinstance(cursor, dict) or cursor.get("is_end"):
                break
            cursor_id = str(cursor.get("id") or "").strip()
            cursor_time = str(
                cursor.get(time_param) or cursor.get("time") or ""
            ).strip()
            cursor_key = (cursor_id, cursor_time)
            if not cursor_id or not cursor_time or cursor_key in visited_cursors:
                if self.logger:
                    self.logger.warning(f"B站通知分页游标无效或重复: url={url}")
                break
            visited_cursors.add(cursor_key)
            params = {
                "build": 0,
                "mobi_app": "web",
                "id": cursor_id,
                time_param: cursor_time,
            }

        if skipped_for_capacity and self.logger:
            self.logger.warning(
                "B站新通知超过积压上限，已保留最新通知并跳过较旧项: "
                f"url={url}, skipped={skipped_for_capacity}, "
                f"limit={self._notification_backlog_limit}"
            )
        return retained

    async def _enqueue_comment_notification(
        self, item: Dict[str, Any], source: str
    ) -> bool:
        user = item.get("user") or {}
        if not isinstance(user, dict):
            return False
        sender_uid = str(user.get("mid") or "").strip()
        content = self._notification_content(item)
        reply_target = self._comment_reply_target(item)
        if not self._current_uid or not sender_uid or not content or not reply_target:
            return False
        if sender_uid == self._current_uid:
            return False
        notification_identity = self._notification_identity(item)
        if not notification_identity:
            return False
        message = {
            "sender_uid": sender_uid,
            "sender_nickname": str(user.get("nickname") or sender_uid),
            "msg_kind": "comment_at" if source == "at" else "comment_reply",
            "notification_source": source,
            "notification_identity": notification_identity,
            "notification_attempt": 0,
            "msg_key": str(item.get("id") or ""),
            # reply_time / at_time 都来自 B站消息流。缺失时保留 0，后续重试
            # 不得把本机时钟作为已发评论的去重边界。
            "timestamp": self._notification_event_time(source, item),
            "content": content,
            "content_type": "text",
            "reply_target": reply_target,
            "video_aid": self._video_aid_from_notification(item),
            "conversation_key": (
                f"comment:{reply_target['type']}:{reply_target['oid']}:"
                f"{reply_target.get('root') or reply_target.get('parent') or item.get('id')}"
            ),
            "raw_event": item,
        }
        try:
            self._message_queue.put_nowait(message)
        except asyncio.QueueFull:
            # 不挤掉队列中的旧消息；保留 claim 并走同一退避重试路径。
            self.retry_comment_notification(message)
        return True

    async def comment_reply_exists(
        self, target: Dict[str, int], text: str, *, sent_after: int = 0
    ) -> Optional[bool]:
        """Check for our exact reply before retrying an ambiguous failed POST.

        ``None`` means the check itself failed, in which case callers should defer
        the retry instead of risking a duplicate comment.
        """
        try:
            from bilibili_api.comment import Comment, CommentResourceType

            current_uid = str(self._current_uid or "").strip()
            root = int(target.get("root") or target.get("parent") or 0)
            if not current_uid or not root:
                return None
            resource_type = CommentResourceType(int(target["type"]))
            comment = Comment(
                oid=int(target["oid"]),
                type_=resource_type,
                rpid=root,
                credential=self._credential,
            )
            for page_index in range(1, 4):
                data = await comment.get_sub_comments(
                    page_index=page_index, page_size=20
                )
                replies = data.get("replies") or [] if isinstance(data, dict) else []
                if not isinstance(replies, list):
                    return None
                for reply in replies:
                    if not isinstance(reply, dict):
                        continue
                    content = reply.get("content") or {}
                    expected_parent = int(
                        target.get("parent") or target.get("root") or 0
                    )
                    reply_parent = int(reply.get("parent") or 0)
                    reply_ctime = int(reply.get("ctime") or 0)
                    if (
                        str(reply.get("mid") or "") == current_uid
                        and isinstance(content, dict)
                        and str(content.get("message") or "") == text
                        and (not expected_parent or reply_parent == expected_parent)
                        and (not sent_after or reply_ctime >= sent_after)
                    ):
                        return True
                page = data.get("page") or {}
                if not replies or (
                    isinstance(page, dict)
                    and int(page.get("num") or 0) * int(page.get("size") or 20)
                    >= int(page.get("count") or 0)
                ):
                    break
            return False
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"检查 B站评论重试幂等状态失败: {exc}")
            return None

    async def send_comment_reply(
        self, target: Dict[str, int], text: str
    ) -> Dict[str, Any]:
        """回复消息中心通知所对应的 B站评论线程。

        评论接口目前需要 WBI/DM 风控签名，因此复用项目固定版本的
        ``bilibili_api`` 请求器。日志记录目标参数和完整接口响应，但不会输出
        Cookie 或 CSRF。
        """
        from bilibili_api.utils.network import Api
        from bilibili_api.utils.utils import get_api

        payload: Dict[str, Any] = {
            "type": target["type"],
            "oid": target["oid"],
            "message": text,
            "plat": 1,
            "statistics": {"appId": 100, "platform": 5},
            "gaia_source": "main_web",
        }
        root = target.get("root")
        parent = target.get("parent")
        if root:
            payload["root"] = root
            # B站接口要求回复一级评论时 parent 与 root 相同。
            payload["parent"] = parent or root

        response_target = {
            "type": payload["type"],
            "oid": payload["oid"],
            "root": payload.get("root"),
            "parent": payload.get("parent"),
        }
        try:
            api_config = get_api("common")["comment"]["send"]
            response = await (
                Api(**api_config, credential=self._credential)
                .update_data(**payload)
                .request(bili_res=True)
            )
            response_text = response.utf8_text()
            if self.logger:
                self.logger.info(
                    f"B站评论发送接口 response: HTTP {response.code}, "
                    f"target={response_target}, body={response_text}"
                )
        except Exception as exc:
            if self.logger:
                self.logger.error(
                    f"B站评论发送接口请求异常: target={response_target}, "
                    f"error={type(exc).__name__}: {exc}"
                )
            raise

        if response.code != 200:
            raise RuntimeError(
                f"B站评论回复 HTTP 错误: status={response.code}, body={response_text}"
            )
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"B站评论接口返回非 JSON: {response_text}") from exc
        if not isinstance(result, dict) or result.get("code") != 0:
            message = (
                result.get("message") or result.get("msg")
                if isinstance(result, dict)
                else "未知错误"
            )
            raise RuntimeError(f"B站评论回复失败: {message}")
        data = result.get("data") or {}
        rpid = (
            data.get("rpid") or data.get("rpid_str") if isinstance(data, dict) else None
        )
        if not rpid:
            raise RuntimeError(
                f"B站评论接口返回成功但未创建评论（缺少 rpid）: {response_text}"
            )
        return result

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
                "timestamp": int(event.timestamp)
                if event.timestamp
                else int(time.time()),
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
                            if bvid
                            else "[分享视频]"
                        )
                elif hasattr(content, "bvid") and content.bvid:
                    message["content"] = (
                        f"[分享视频] https://www.bilibili.com/video/{content.bvid}"
                    )
                else:
                    message["content"] = "[分享视频]"
                message["content_type"] = "text"

            # 放入队列
            try:
                self._message_queue.put_nowait(message)
            except asyncio.QueueFull:
                # 队列满时丢弃最旧的私信，但评论通知的 claim 不能静默
                # 丢失，须重新进入受限的退避队列。
                evicted = self._message_queue.get_nowait()
                if str(evicted.get("notification_identity") or "").strip():
                    self.retry_comment_notification(evicted)
                self._message_queue.put_nowait(message)

            if self.logger:
                self.logger.info(
                    f"收到 B站私信 [{msg_kind}] from {sender_uid} ({nickname})"
                )

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
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                resp = await client.get(
                    url,
                    cookies=self._cookies(),
                    headers={
                        "Referer": "https://www.bilibili.com",
                        "Accept-Encoding": self.BILIBILI_ACCEPT_ENCODING,
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
