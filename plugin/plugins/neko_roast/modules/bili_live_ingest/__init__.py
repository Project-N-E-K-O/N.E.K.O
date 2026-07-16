"""Bilibili live event normalization for v0.1."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any

from ...core.contracts import LiveEvent, LiveRoomStatus, ViewerEvent
from ...core.runtime_timeline import record_timeline
from .._base import BaseModule

SUPPORT_EVENT_DEDUPE_SECONDS = 0.35


class _ListenerLog:
    """把 DanmakuListener 的日志收敛到 audit：info/debug 丢弃（避免刷屏+隐私），warning/error 入 audit。"""

    def __init__(self, audit: Any) -> None:
        self._audit = audit

    def info(self, msg: Any = "", *args: Any, **kwargs: Any) -> None:
        return None

    def debug(self, msg: Any = "", *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, msg: Any = "", *args: Any, **kwargs: Any) -> None:
        if self._audit is not None:
            self._audit.record("live_listener", str(msg)[:200], level="warning")

    def error(self, msg: Any = "", *args: Any, **kwargs: Any) -> None:
        if self._audit is not None:
            self._audit.record("live_listener", str(msg)[:200], level="error")


class BiliLiveIngestModule(BaseModule):
    id = "bili_live_ingest"
    title = "B站直播输入"

    def __init__(self) -> None:
        super().__init__()
        # 吞并自 plugin/plugins/bilibili_danmaku 的 DanmakuListener（见 live-center 计划）。
        self._listener: Any = None
        self._listener_task: "asyncio.Task[Any] | None" = None
        self._lifecycle_lock = asyncio.Lock()
        self._listener_generation = 0
        self._listener_ready_timeout = 20.0
        self._room_id: int = 0
        # lookup 反 -352：临时 buvid3 缓存 + 房间状态短期缓存（见 _lookup_room_status_sync）
        self._lookup_buvid3: str = ""
        self._lookup_buvid3_ts: float = 0.0
        self._lookup_buvid3_ttl: float = 6 * 3600.0
        self._room_status_cache: "dict[int, tuple[LiveRoomStatus, float]]" = {}
        self._room_status_ttl: float = 60.0
        self._last_event_at: float = 0.0
        self._last_event_type: str = ""
        self._recent_support_event_keys: dict[str, float] = {}

    async def teardown(self) -> None:
        await self.stop_listening()
        await super().teardown()

    def status(self) -> dict[str, Any]:
        listener_state = self.listener_state()
        return {
            "enabled": self.enabled,
            "listening": self.is_listening(),
            "room_id": self._room_id,
            "last_event_at": self._last_event_at,
            "last_event_type": self._last_event_type,
            "reconnect_count": int(listener_state.get("reconnect_count") or 0),
            "last_packet_at": float(listener_state.get("last_packet_at") or 0.0),
        }

    def is_listening(self) -> bool:
        if self._listener is None or self._listener_task is None or self._listener_task.done():
            return False
        try:
            return self._listener.get_connection_state().get("state") == "receiving"
        except Exception:
            return False

    def listener_state(self) -> dict[str, Any]:
        if self._listener is None:
            return {"state": "disconnected", "room_id": self._room_id, "viewer_count": 0}
        try:
            return self._listener.get_connection_state()
        except Exception:
            return {"state": "unknown", "room_id": self._room_id, "viewer_count": 0}

    async def start_listening(self, room_id: int) -> bool:
        """启动真实弹幕监听（匿名只读）。返回是否成功创建监听任务。"""
        room_id = int(room_id or 0)
        if room_id <= 0:
            return False
        async with self._lifecycle_lock:
            await self._stop_listening_locked()
            audit = self.ctx.audit if self.ctx else None
            try:
                from .danmaku_core import DanmakuListener
            except Exception as exc:
                if audit is not None:
                    audit.record("live_listener_import_failed", f"{type(exc).__name__}: {exc}", level="error")
                return False
            self._listener_generation += 1
            generation = self._listener_generation

            async def on_live() -> None:
                await self._on_live(generation=generation)

            async def on_preparing() -> None:
                await self._on_preparing(generation=generation)

            async def on_error(exc: Any) -> None:
                await self._on_error(exc, generation=generation)

            callbacks = {
                # 富模型 on_event（带 get_score 打分）→ live_events 中枢窗口择优；不再用轻量
                # on_danmaku 直连 pipeline，避免同一条弹幕被两条路各锐评一次。
                "on_event": lambda cmd, event: self._on_live_event(cmd, event, generation=generation),
                "on_gift": lambda event: self._on_gift_event(event, generation=generation),
                "on_sc": lambda event: self._on_super_chat_event(event, generation=generation),
                "on_live": on_live,
                "on_preparing": on_preparing,
                "on_error": on_error,
            }
            listener = DanmakuListener(
                room_id=room_id,
                # 登录态（若有）让弹幕连接走登录会话，更稳、低风控；未登录=匿名只读（临时 buvid3 绕风控）。
                credential=getattr(self.ctx, "bili_credential", None) if self.ctx else None,
                logger=_ListenerLog(audit),
                callbacks=callbacks,
            )
            task = asyncio.create_task(listener.start())
            self._listener = listener
            self._listener_task = task
            self._room_id = room_id
            task.add_done_callback(lambda done: self._listener_task_done(generation, listener, done))

        ready_task = asyncio.create_task(listener.wait_until_ready())
        try:
            done, _pending = await asyncio.wait(
                {task, ready_task},
                timeout=self._listener_ready_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            ready = ready_task in done and not ready_task.cancelled() and ready_task.exception() is None
        except asyncio.CancelledError:
            await self._stop_generation(generation)
            raise
        finally:
            if not ready_task.done():
                ready_task.cancel()
            try:
                await ready_task
            except asyncio.CancelledError:
                pass

        async with self._lifecycle_lock:
            current = generation == self._listener_generation and self._listener is listener and self._listener_task is task
            if ready and current and not task.done():
                if audit is not None:
                    audit.record("live_listener_started", "danmaku listener authenticated", detail={"room_id": room_id})
                return True
        await self._stop_generation(generation)
        return False

    async def stop_listening(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_listening_locked()

    async def _stop_generation(self, generation: int) -> None:
        async with self._lifecycle_lock:
            if generation != self._listener_generation:
                return
            await self._stop_listening_locked()

    async def _stop_listening_locked(self) -> None:
        self._listener_generation += 1
        self._recent_support_event_keys.clear()
        self._last_event_at = 0.0
        self._last_event_type = ""
        listener = self._listener
        task = self._listener_task
        self._listener = None
        self._listener_task = None
        # 先取消监听任务（打断接收循环），再关闭——都加超时，避免 ws close 握手拖慢断开。
        if task is not None and not task.done():
            task.cancel()
        if listener is not None:
            try:
                await asyncio.wait_for(listener.stop(), timeout=2.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.ctx is not None:
                    self.ctx.audit.record("live_listener_stop_failed", str(exc)[:200], level="warning")
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.CancelledError:
                # The task was cancelled above as part of the normal shutdown path.
                pass
            except Exception as exc:
                if self.ctx is not None:
                    self.ctx.audit.record("live_listener_task_failed", str(exc)[:200], level="warning")
        if self.ctx is not None and listener is not None:
            self.ctx.audit.record("live_listener_stopped", "danmaku listener stopped", detail={"room_id": self._room_id})

    def _listener_task_done(self, generation: int, listener: Any, task: "asyncio.Task[Any]") -> None:
        if task.cancelled():
            exc = None
        else:
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                exc = None
        if generation != self._listener_generation or self._listener is not listener or self._listener_task is not task:
            return
        self._listener = None
        self._listener_task = None
        if self.ctx is None:
            return
        if exc is not None:
            self.ctx.audit.record("live_listener_task_failed", type(exc).__name__, level="warning")
            return
        live_ended = bool(getattr(listener, "_live_ended", False))
        try:
            state = listener.get_connection_state()
        except Exception:
            state = {}
        last_packet_at = float(state.get("last_packet_at") or 0.0)
        last_packet_age = max(0.0, time.time() - last_packet_at) if last_packet_at else 0.0
        self.ctx.audit.record(
            "live_listener_task_ended",
            "live ended" if live_ended else "listener task ended unexpectedly",
            level="info" if live_ended else "warning",
            detail={
                "generation": generation,
                "reconnect_count": int(state.get("reconnect_count") or 0),
                "last_packet_age_seconds": round(last_packet_age, 1),
                "last_event_type": self._last_event_type,
            },
        )

    # 命令名 → LiveEvent.type 路由键（见 core/contracts.LiveEvent）。未列出的命令回落 cmd 小写。
    _CMD_TO_TYPE = {
        "DANMU_MSG": "danmaku",
        "SEND_GIFT": "gift",
        "COMBO_SEND": "gift",
        "SUPER_CHAT_MESSAGE": "super_chat",
        "SUPER_CHAT_MESSAGE_JPN": "super_chat",
        "GUARD_BUY": "guard",
        "INTERACT_WORD": "entry",
    }

    def _on_live_event(self, cmd: str, event: Any, *, generation: int | None = None) -> None:
        """富模型直播事件回调 → 包成 ``LiveEvent`` 发布到 ``EventBus``，由订阅者按类型消费。

        同步、非阻塞：``publish`` 只做同步派发（订阅者内部各自 fire-and-forget），不拖慢弹幕
        接收循环。``danmaku_core`` 对 DANMU_MSG/SEND_GIFT/SC/INTERACT_WORD/增强指令都发
        ``on_event``，全部发布到总线；``live_events`` 订阅 danmaku/gift/super_chat/guard
        参与窗口择优；其他事件族 handler 可各自订阅（见 docs/development.md「直播事件中枢」）。
        """
        if generation is not None and generation != self._listener_generation:
            return
        if not self.ctx or not self._owns_current_live_session():
            return
        if isinstance(event, dict) and event.get("room_id") in (0, None):
            event["room_id"] = self._room_id
        elif event is not None and getattr(event, "room_id", 0) in (0, None):
            try:
                event.room_id = self._room_id
            except Exception as exc:
                self.ctx.audit.record("live_event_room_id_fill_failed", str(exc)[:200], level="warning")
        bus = getattr(self.ctx, "event_bus", None)
        if bus is None:
            return
        live_event = self._to_live_event(cmd, event)
        live_event.session_generation = max(
            0,
            int(getattr(self.ctx, "_live_session_generation", 0) or 0),
        )
        if live_event.type in {"gift", "super_chat", "guard"}:
            record_timeline(
                self.ctx,
                live_event,
                stage="ingest",
                status="received",
                reason=f"support.{live_event.type}",
                route=self.id,
            )
        if self._is_duplicate_support_event(live_event):
            record_timeline(
                self.ctx,
                live_event,
                stage="ingest",
                status="dropped",
                reason="ingest.duplicate_support_event",
                route=self.id,
            )
            return
        self._last_event_at = live_event.ts or time.time()
        self._last_event_type = live_event.type
        if live_event.type in {"gift", "super_chat", "guard"}:
            record_timeline(
                self.ctx,
                live_event,
                stage="event_bus",
                status="published",
                reason=f"support.{live_event.type}",
                route=self.id,
            )
        bus.publish(live_event.type, live_event)

    def _owns_current_live_session(self) -> bool:
        """Reject events from a provider generation that no longer owns input."""
        if self.ctx is None or getattr(self.ctx, "_stopping", False) is True:
            return False
        router = getattr(self.ctx, "live_provider", None)
        if router is None:
            return True
        try:
            if getattr(router, "platform", "") != "bilibili":
                return False
            provider_for = getattr(router, "provider_for", None)
            if callable(provider_for) and provider_for("bilibili") is not self:
                return False
            configured_room_ref = getattr(router, "configured_room_ref", None)
            if callable(configured_room_ref):
                room_ref = str(configured_room_ref() or "").strip()
                if room_ref and room_ref != str(self._room_id):
                    return False
        except Exception:
            return False
        return True

    def _on_gift_event(self, event: Any, *, generation: int | None = None) -> None:
        """Fallback path for Bilibili's lightweight gift callback."""
        self._on_live_event("SEND_GIFT", event, generation=generation)

    def _on_super_chat_event(self, event: Any, *, generation: int | None = None) -> None:
        """Fallback path for Bilibili's lightweight Super Chat callback."""
        self._on_live_event("SUPER_CHAT_MESSAGE", event, generation=generation)

    def _to_live_event(self, cmd: str, event: Any) -> LiveEvent:
        """把富模型 + 命令名包成统一信封。``raw`` 保留富模型，供需要完整字段（如
        ``get_score()``）的 handler（如 ``live_events`` 中枢）解包使用。"""
        normalized_cmd = self._normalize_cmd(cmd)
        event_type = self._CMD_TO_TYPE.get(normalized_cmd, normalized_cmd.lower())
        is_typed_support = event_type in {"gift", "super_chat", "guard"}
        if isinstance(event, dict):
            payload = dict(event)
            payload["event_type"] = event_type
            payload["room_id"] = payload.get("room_id") or self._room_id
            uid = str(payload.get("uid") or payload.get("user_id") or "").strip()
            nickname = str(payload.get("nickname") or payload.get("user_name") or payload.get("uname") or "")
            text = str(payload.get("danmaku_text") or payload.get("text") or payload.get("message") or "")
            payload["uid"] = uid
            payload["nickname"] = nickname
            payload["danmaku_text"] = text
            payload["event_label"] = self._event_label(event_type, text)
            payload["raw_type"] = normalized_cmd
            if is_typed_support:
                payload["support_verified"] = True
                payload["support_evidence"] = "bilibili_typed_command"
                payload["provider_event_type"] = normalized_cmd
            return LiveEvent(type=event_type, uid=uid, payload=payload, source="live", ts=time.time(), raw=payload)
        uid = str(getattr(event, "uid", "") or "").strip()
        nickname = str(getattr(event, "nickname", "") or "")
        text = str(getattr(event, "text", "") or "")
        payload = {
            "uid": uid,
            "username": nickname,
            "nickname": nickname,
            "text": text,
            "event_label": self._event_label(event_type, text),
            "raw_type": normalized_cmd,
            "guard_level": getattr(event, "guard_level", 0),
            "room_id": getattr(event, "room_id", 0) or self._room_id,
            "cmd": normalized_cmd,
        }
        if is_typed_support:
            payload["support_verified"] = True
            payload["support_evidence"] = "bilibili_typed_command"
            payload["provider_event_type"] = normalized_cmd
        return LiveEvent(type=event_type, uid=uid, payload=payload, source="live", ts=time.time(), raw=event)

    @staticmethod
    def _normalize_cmd(cmd: Any) -> str:
        return str(cmd or "").split(":", 1)[0].strip()

    def _is_duplicate_support_event(self, live_event: LiveEvent) -> bool:
        if live_event.type not in {"gift", "super_chat", "guard"}:
            return False
        now = float(live_event.ts or time.time())
        expired = [
            key
            for key, seen_at in self._recent_support_event_keys.items()
            if now - seen_at > SUPPORT_EVENT_DEDUPE_SECONDS
        ]
        for key in expired:
            self._recent_support_event_keys.pop(key, None)
        key = self._support_event_key(live_event)
        if not key:
            return False
        last_seen = self._recent_support_event_keys.get(key)
        if last_seen is not None and 0 <= now - last_seen <= SUPPORT_EVENT_DEDUPE_SECONDS:
            return True
        self._recent_support_event_keys[key] = now
        return False

    @staticmethod
    def _support_event_key(live_event: LiveEvent) -> str:
        raw = live_event.raw
        payload = live_event.payload if isinstance(live_event.payload, dict) else {}
        gift = getattr(raw, "gift", None) if raw is not None else None
        command = BiliLiveIngestModule._normalize_cmd(
            payload.get("raw_cmd") or payload.get("cmd") or payload.get("raw_type") or ""
        )
        uid = str(live_event.uid or payload.get("uid") or payload.get("user_id") or "")
        if live_event.type == "super_chat":
            text = str(
                payload.get("danmaku_text")
                or payload.get("text")
                or payload.get("message")
                or getattr(raw, "text", "")
                or ""
            )
            return "|".join(part.strip()[:80] for part in (live_event.type, command, uid, text))
        parts = [
            live_event.type,
            command,
            uid,
            str(
                payload.get("gift_name")
                or payload.get("giftName")
                or getattr(gift, "gift_name", "")
                or payload.get("danmaku_text")
                or payload.get("text")
                or ""
            ),
            str(payload.get("gift_count") or payload.get("num") or getattr(gift, "num", "") or ""),
            str(
                payload.get("gift_value")
                or payload.get("total_coin")
                or getattr(gift, "total_coin", "")
                or getattr(gift, "price", "")
                or ""
            ),
        ]
        return "|".join(part.strip()[:80] for part in parts)

    @staticmethod
    def _event_label(event_type: str, text: str) -> str:
        cleaned = str(text or "").strip()
        if event_type == "entry":
            if "关注" in cleaned:
                return "关注了主播"
            return "进入直播间"
        return cleaned

    async def _on_live(self, *, generation: int | None = None) -> None:
        if generation is not None and generation != self._listener_generation:
            return
        self._mark_room_live_status("live")
        if self.ctx is not None:
            self.ctx.audit.record("live_room_live", "live started", detail={"room_id": self._room_id})

    async def _on_preparing(self, *, generation: int | None = None) -> None:
        if generation is not None and generation != self._listener_generation:
            return
        self._mark_room_live_status("offline")
        if self.ctx is not None:
            self.ctx.audit.record("live_room_preparing", "live ended", detail={"room_id": self._room_id})

    def _mark_room_live_status(self, live_status: str) -> None:
        if self.ctx is None:
            return
        context = getattr(self.ctx, "live_room_context", None)
        if not isinstance(context, dict):
            context = {}
        context = dict(context)
        context["platform"] = "bilibili"
        if self._room_id > 0:
            context["room_ref"] = str(self._room_id)
            context["room_id"] = self._room_id
        context["live_status"] = live_status
        self.ctx.live_room_context = context

    async def _on_error(self, exc: Any, *, generation: int | None = None) -> None:
        if generation is not None and generation != self._listener_generation:
            return
        if self.ctx is not None:
            self.ctx.audit.record("live_listener_error", str(exc)[:200], level="warning")

    def normalize(self, payload: dict[str, Any]) -> ViewerEvent:
        uid = str(payload.get("uid") or payload.get("user_id") or "").strip()
        nickname = str(payload.get("nickname") or payload.get("uname") or payload.get("user_name") or "").strip()
        avatar_url = str(payload.get("avatar_url") or payload.get("face_url") or payload.get("face") or "").strip()
        text = str(payload.get("danmaku_text") or payload.get("text") or payload.get("content") or "").strip()
        target_lanlan = str(payload.get("target_lanlan") or payload.get("lanlan_name") or "").strip()
        return ViewerEvent(
            uid=uid,
            nickname=nickname,
            avatar_url=avatar_url,
            danmaku_text=text,
            target_lanlan=target_lanlan,
            source="live_danmaku",
            live_mode=self.ctx.config.live_mode if self.ctx else "co_stream",
            trace_id=str(payload.get("trace_id") or "").strip(),
            raw=dict(payload),
        )

    async def lookup_room_status(self, room_id: int) -> LiveRoomStatus:
        if room_id <= 0:
            return LiveRoomStatus(room_id=0, ok=False, message="room_id must be positive")
        try:
            return await asyncio.to_thread(self._lookup_room_status_sync, room_id)
        except Exception as exc:
            return LiveRoomStatus(
                room_id=room_id,
                ok=False,
                message=f"live room lookup failed: {type(exc).__name__}",
            )

    # 反 -352：lookup 走的 getInfoByRoom（HTTP）补上临时 buvid3 + 浏览器 headers。
    # getInfoByRoom 不需要 WBI 签名（弹幕 WS 的 _get_real_room_id 调它也没签）。
    _BROWSER_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://live.bilibili.com",
    }

    @staticmethod
    def _parse_buvid3_from_cookies(set_cookie_lines: "list[str]") -> str:
        """从 Set-Cookie 行里抽 buvid3 值；没有返回空串。"""
        for raw in set_cookie_lines or []:
            for part in str(raw).split(";"):
                part = part.strip()
                if part.startswith("buvid3="):
                    return part[len("buvid3="):]
        return ""

    def _fetch_buvid3_sync(self) -> str:
        """访问 B站首页拿临时 buvid3（绕 -352 风控）。失败返回空串。"""
        try:
            request = urllib.request.Request(
                "https://www.bilibili.com",
                headers={"User-Agent": self._BROWSER_HEADERS["User-Agent"], "Accept": "text/html"},
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                lines = response.headers.get_all("Set-Cookie") or []
            return self._parse_buvid3_from_cookies(lines)
        except Exception:
            return ""

    def _get_buvid3(self, force: bool = False) -> str:
        now = time.time()
        if not force and self._lookup_buvid3 and (now - self._lookup_buvid3_ts) < self._lookup_buvid3_ttl:
            return self._lookup_buvid3
        fetched = self._fetch_buvid3_sync()
        if fetched:
            self._lookup_buvid3 = fetched
            self._lookup_buvid3_ts = now
        return self._lookup_buvid3  # 即便本次没拿到也用旧值（可能空）

    def _credential_cookie(self) -> str:
        """登录态（若有）的完整 Cookie 串；未登录返回空串。用于 lookup 过 -352。"""
        cred = getattr(self.ctx, "bili_credential", None) if self.ctx else None
        if cred is None:
            return ""
        sessdata = str(getattr(cred, "sessdata", "") or "")
        if not sessdata:
            return ""
        parts = [f"SESSDATA={sessdata}"]
        for name, attr in (("bili_jct", "bili_jct"), ("DedeUserID", "dedeuserid"), ("buvid3", "buvid3")):
            value = str(getattr(cred, attr, "") or "")
            if value:
                parts.append(f"{name}={value}")
        return "; ".join(parts)

    def _lookup_room_status_sync(self, room_id: int) -> LiveRoomStatus:
        now = time.time()
        cached = self._room_status_cache.get(room_id)
        if cached and (now - cached[1]) < self._room_status_ttl:
            return cached[0]
        status, code = self._do_room_lookup(room_id, self._get_buvid3())
        if code == -352:
            # 风控：刷新 buvid3 再试一次（只重试一次，别硬刷加重风控）
            status, code = self._do_room_lookup(room_id, self._get_buvid3(force=True))
        if status.ok:
            self._room_status_cache[room_id] = (status, now)
        return status

    def _do_room_lookup(self, room_id: int, buvid3: str) -> "tuple[LiveRoomStatus, int]":
        """单次 getInfoByRoom 请求。返回 (status, B站 code)；网络/解析错误 code=-1。"""
        url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id={room_id}"
        headers = dict(self._BROWSER_HEADERS)
        headers["Referer"] = f"https://live.bilibili.com/{room_id}"
        # 登录态优先：带完整登录 cookie（过 -352）；未登录回落临时 buvid3（同现状）。
        cookie = self._credential_cookie() or (f"buvid3={buvid3}" if buvid3 else "")
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError) as exc:
            return LiveRoomStatus(room_id=room_id, ok=False, message=f"network error: {type(exc).__name__}"), -1
        except json.JSONDecodeError:
            return LiveRoomStatus(room_id=room_id, ok=False, message="invalid response from Bilibili"), -1

        code = int(payload.get("code") or 0)
        if code != 0:
            raw_msg = str(payload.get("message") or payload.get("msg") or "")
            return LiveRoomStatus(room_id=room_id, ok=False, message=self._friendly_lookup_message(code, raw_msg)), code

        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        room_info = data.get("room_info") if isinstance(data.get("room_info"), dict) else {}
        anchor_info = data.get("anchor_info") if isinstance(data.get("anchor_info"), dict) else {}
        base_info = anchor_info.get("base_info") if isinstance(anchor_info.get("base_info"), dict) else {}
        real_room_id = int(room_info.get("room_id") or room_id)
        live_status = self._normalize_live_status(room_info.get("live_status"))
        title = str(room_info.get("title") or "").strip()
        anchor_name = str(base_info.get("uname") or base_info.get("name") or "").strip()
        message = "live room found"
        if live_status == "live":
            message = "live room is streaming"
        elif live_status == "offline":
            message = "live room is offline"
        return LiveRoomStatus(
            room_id=real_room_id,
            ok=True,
            title=title,
            anchor_name=anchor_name,
            live_status=live_status,
            message=message,
        ), 0

    @staticmethod
    def _friendly_lookup_message(code: int, raw_message: str) -> str:
        """把 B站查询失败码翻成人话。最常见的是 -352：匿名请求被反爬风控拦截
        （查询走的 HTTP 路径没有像弹幕 WS 那样的临时 buvid3 反 -352 措施）。"""
        raw = (raw_message or "").strip()
        if code == -352 or raw == "-352":
            return (
                "B站风控校验失败（-352）：匿名查询被反爬拦截。"
                "可稍后重试 / 更换网络 / 登录后再查；直播间监听（弹幕）通常仍可用。"
            )
        if code in (1, 19002000) or "不存在" in raw or "未找到" in raw:
            return f"未找到该直播间（code={code}），请确认房间号是否正确。"
        if raw:
            return f"B站查询失败（code={code}）：{raw}"
        return f"B站查询失败（code={code}）。"

    @staticmethod
    def _normalize_live_status(value: Any) -> str:
        try:
            status = int(value)
        except (TypeError, ValueError):
            return "unknown"
        if status == 1:
            return "live"
        if status == 0:
            return "offline"
        if status == 2:
            return "rounding"
        return "unknown"


def _safe_non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0
