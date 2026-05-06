"""
B站私信 N.E.K.O 插件

通过 bilibili_api 监听 B站私信，使用 AI 自动回复。
支持文本、图片、分享视频等消息类型。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin.sdk.plugin import (
    NekoPluginBase, lifecycle, neko_plugin, plugin_entry,
    Ok, Err, SdkError,
)

from .bili_client import BiliDMClient
from .permission import PermissionManager


@neko_plugin
class BiliDMPlugin(NekoPluginBase):
    """B站私信 N.E.K.O 插件

    通过 bilibili_api 监听 B站私信，使用 AI 自动回复。
    支持文本、图片、分享视频等消息类型。
    """

    SESSION_IDLE_TIMEOUT_SECONDS = 300
    SESSION_SWEEP_INTERVAL_SECONDS = 30

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        # B站客户端
        self.bili_client: Optional[BiliDMClient] = None
        self.permission_mgr: Optional[PermissionManager] = None

        # 运行状态
        self._running = False
        self._message_task: Optional[asyncio.Task] = None
        self._session_housekeeping_task: Optional[asyncio.Task] = None
        self._handler_tasks: set[asyncio.Task] = set()

        # AI 会话管理
        self._user_sessions: dict[str, dict[str, Any]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_locks_guard = asyncio.Lock()

        # 并发控制
        self._max_concurrent_messages = 3
        self._message_concurrency = asyncio.Semaphore(self._max_concurrent_messages)
        self._ai_connect_timeout_seconds = 10.0
        self._ai_turn_timeout_seconds = 60.0
        self._handler_shutdown_timeout_seconds = 10.0

        # 管理员 UID
        self._admin_uid: Optional[str] = None

        # 配置缓存
        self._cfg: dict = {}

    def _refresh_admin_uid(self) -> None:
        """刷新管理员 UID"""
        self._admin_uid = None
        if not self.permission_mgr:
            return
        for user in self.permission_mgr.list_users():
            if user.get("level") == "admin":
                uid = str(user.get("uid") or "").strip()
                if uid:
                    self._admin_uid = uid
                    return

    @staticmethod
    def _build_session_key(sender_uid: str) -> str:
        return f"bili_dm:{sender_uid}"

    async def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        async with self._session_locks_guard:
            lock = self._session_locks.get(session_key)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_key] = lock
            return lock

    def _track_handler_task(self, task: asyncio.Task) -> None:
        self._handler_tasks.add(task)
        task.add_done_callback(self._on_handler_task_done)

    def _on_handler_task_done(self, task: asyncio.Task) -> None:
        self._handler_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.error(f"消息处理任务失败: {exc}")

    async def _run_message_handler(self, message: Dict[str, Any]) -> None:
        if not self._running:
            return
        session_key = self._build_session_key(message["sender_uid"])
        async with self._message_concurrency:
            session_lock = await self._get_session_lock(session_key)
            async with session_lock:
                if not self._running:
                    return
                await self._handle_message(message)

    async def _wait_session_response_complete(self, session: Any, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if not getattr(session, "_is_responding", False):
                return True
        return False

    # ===== Lifecycle =====

    @lifecycle(id="startup")
    async def startup(self, **_):
        """插件启动时初始化"""
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg
        bili_cfg = cfg.get("bilibili_dm", {})

        # 初始化权限管理器（优先从 store 加载，回退到 TOML 配置）
        store_users_result = await self.store.get("trusted_users")
        if isinstance(store_users_result, Ok) and store_users_result.value is not None:
            trusted_users = store_users_result.value
            self.logger.info(f"从 store 加载 {len(trusted_users)} 个信任用户")
        else:
            trusted_users = bili_cfg.get("trusted_users", [])
        self.permission_mgr = PermissionManager(trusted_users)

        # 获取管理员 UID
        self._refresh_admin_uid()

        # 读取配置
        self._max_concurrent_messages = max(1, int(bili_cfg.get("max_concurrent_messages", 3) or 3))
        self._message_concurrency = asyncio.Semaphore(self._max_concurrent_messages)
        self._ai_connect_timeout_seconds = max(1.0, float(bili_cfg.get("ai_connect_timeout_seconds", 10.0) or 10.0))
        self._ai_turn_timeout_seconds = max(5.0, float(bili_cfg.get("ai_turn_timeout_seconds", 60.0) or 60.0))
        self._handler_shutdown_timeout_seconds = max(1.0, float(bili_cfg.get("handler_shutdown_timeout_seconds", 10.0) or 10.0))

        # 初始化 B站客户端
        sesdata = bili_cfg.get("sesdata", "")
        if not sesdata:
            self.logger.warning("B站 Cookie (SESSDATA) 未配置，请在 plugin.toml 中填写")

        self.bili_client = BiliDMClient(
            sesdata=sesdata,
            bili_jct=bili_cfg.get("bili_jct", ""),
            buvid3=bili_cfg.get("buvid3", ""),
            dedeuserid=bili_cfg.get("dedeuserid", ""),
            ac_time_value=bili_cfg.get("ac_time_value", ""),
            logger=self.logger,
        )
        self.logger.info("B站私信客户端已初始化")

        return Ok({"status": "initialized"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        """插件关闭时清理资源"""
        await self._stop_runtime()
        await self._flush_all_sessions(reason="shutdown")

        if self._session_housekeeping_task:
            self._session_housekeeping_task.cancel()
            try:
                await self._session_housekeeping_task
            except asyncio.CancelledError:
                pass
            self._session_housekeeping_task = None

        self.logger.info("B站私信插件已停止")
        return Ok({"status": "shutdown"})

    # ===== Plugin Entries =====

    @plugin_entry(
        id="start_listening",
        name="开始监听",
        description="开始监听 B站私信并自动回复。",
        input_schema={"type": "object", "properties": {}},
    )
    async def start_listening(self, **_):
        """开始监听 B站私信"""
        if self._running:
            return Ok({"status": "already_running"})

        if not self.bili_client:
            return Err(SdkError("NOT_INITIALIZED: B站客户端未初始化"))

        try:
            await self.bili_client.connect()

            self._running = True
            self._message_task = asyncio.create_task(self._process_messages())

            if self._session_housekeeping_task is None or self._session_housekeeping_task.done():
                self._session_housekeeping_task = asyncio.create_task(self._session_housekeeping_loop())

            self.logger.info("B站私信监听已启动")
            return Ok({"status": "started"})
        except Exception as e:
            self.logger.exception("启动 B站私信监听失败")
            return Err(SdkError(f"START_ERROR: 启动失败: {e}"))

    @plugin_entry(
        id="stop_listening",
        name="停止监听",
        description="停止监听 B站私信。",
        input_schema={"type": "object", "properties": {}},
    )
    async def stop_listening(self, **_):
        """停止监听 B站私信"""
        if not self._running and not self._message_task:
            return Ok({"status": "not_running"})

        await self._stop_runtime()
        self.logger.info("B站私信监听已停止")
        return Ok({"status": "stopped"})

    async def _stop_runtime(self):
        """停止运行时资源"""
        self._running = False

        if self._message_task:
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
            self._message_task = None

        if self._handler_tasks:
            handler_tasks = list(self._handler_tasks)
            for task in handler_tasks:
                task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*handler_tasks, return_exceptions=True),
                    timeout=self._handler_shutdown_timeout_seconds,
                )
            except asyncio.TimeoutError:
                self.logger.warning(f"等待 {len(handler_tasks)} 个消息处理任务停止超时")
            self._handler_tasks.clear()

        if self.bili_client:
            await self.bili_client.disconnect()

        self._session_locks.clear()

    @plugin_entry(
        id="send_message",
        name="发送私信",
        description="向指定 B站用户发送一条私信。",
        input_schema={
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "目标用户 UID",
                },
                "message": {
                    "type": "string",
                    "description": "要发送的消息内容",
                },
            },
            "required": ["user_id", "message"],
        },
    )
    async def send_message(self, user_id: str, message: str, **_):
        """发送私信到指定 B站用户"""
        if not self.bili_client:
            return Err(SdkError("NOT_INITIALIZED: B站客户端未初始化"))

        try:
            uid = str(user_id or "").strip()
            if not uid:
                return Err(SdkError("INVALID_ARGUMENT: user_id 不能为空"))
            if not uid.isdigit():
                return Err(SdkError("INVALID_ARGUMENT: user_id 必须是纯数字"))

            msg_text = str(message or "").strip()
            if not msg_text:
                return Err(SdkError("INVALID_ARGUMENT: message 不能为空"))

            await self.bili_client.send_text(uid, msg_text)
            self.logger.info(f"已发送私信给 {uid}: {msg_text[:100]}")
            return Ok({"user_id": uid, "message": msg_text})
        except Exception as e:
            self.logger.error(f"发送私信失败: {e}")
            return Err(SdkError(f"SEND_FAILED: 发送私信失败: {e}"))

    @plugin_entry(
        id="add_trusted_user",
        name="添加信任用户",
        description="添加一个信任的 B站用户到白名单。",
        input_schema={
            "type": "object",
            "properties": {
                "uid": {
                    "type": "string",
                    "description": "B站用户 UID",
                },
                "level": {
                    "type": "string",
                    "description": "权限等级: admin, trusted, normal",
                    "default": "trusted",
                },
                "nickname": {
                    "type": "string",
                    "description": "用户昵称（可选）",
                    "default": "",
                },
            },
            "required": ["uid"],
        },
    )
    async def add_trusted_user(self, uid: str, level: str = "trusted", nickname: str = "", **_):
        """添加信任用户并持久化到 store"""
        if not self.permission_mgr:
            return Err(SdkError("NOT_INITIALIZED: 权限管理器未初始化"))

        uid_str = str(uid or "").strip()
        if not uid_str:
            return Err(SdkError("INVALID_ARGUMENT: uid 不能为空"))
        if not uid_str.isdigit():
            return Err(SdkError("INVALID_ARGUMENT: uid 必须是纯数字"))

        user_nickname = "" if level == "admin" else nickname
        self.permission_mgr.add_user(uid_str, level, user_nickname)
        self._refresh_admin_uid()

        # 使现有会话失效
        session_key = self._build_session_key(uid_str)
        if session_key in self._user_sessions:
            user_data = self._user_sessions.pop(session_key, None)
            if user_data and user_data.get("session"):
                try:
                    await user_data["session"].close()
                except Exception:
                    pass

        self.logger.info(f"已添加信任用户: {uid_str}, 权限: {level}")

        success = await self._save_trusted_users()
        result_data = {"uid": uid_str, "level": level, "persisted": success}
        if user_nickname:
            result_data["nickname"] = user_nickname
        if not success:
            result_data["warning"] = "已添加到内存，但持久化失败"
        return Ok(result_data)

    @plugin_entry(
        id="remove_trusted_user",
        name="移除信任用户",
        description="从白名单中移除一个 B站用户。",
        input_schema={
            "type": "object",
            "properties": {
                "uid": {
                    "type": "string",
                    "description": "B站用户 UID",
                },
            },
            "required": ["uid"],
        },
    )
    async def remove_trusted_user(self, uid: str, **_):
        """移除信任用户并持久化到 store"""
        if not self.permission_mgr:
            return Err(SdkError("NOT_INITIALIZED: 权限管理器未初始化"))

        uid_str = str(uid or "").strip()
        if not uid_str:
            return Err(SdkError("INVALID_ARGUMENT: uid 不能为空"))

        self.permission_mgr.remove_user(uid_str)
        self._refresh_admin_uid()

        # 使现有会话失效
        session_key = self._build_session_key(uid_str)
        if session_key in self._user_sessions:
            user_data = self._user_sessions.pop(session_key, None)
            if user_data and user_data.get("session"):
                try:
                    await user_data["session"].close()
                except Exception:
                    pass

        self.logger.info(f"已移除信任用户: {uid_str}")

        success = await self._save_trusted_users()
        result = {"uid": uid_str, "persisted": success}
        if not success:
            result["warning"] = "已从内存移除，但持久化失败"
        return Ok(result)

    @plugin_entry(
        id="set_user_nickname",
        name="设置用户昵称",
        description="为信任用户设置专属称呼。",
        input_schema={
            "type": "object",
            "properties": {
                "uid": {
                    "type": "string",
                    "description": "B站用户 UID",
                },
                "nickname": {
                    "type": "string",
                    "description": "昵称（留空则清除昵称）",
                },
            },
            "required": ["uid"],
        },
    )
    async def set_user_nickname(self, uid: str, nickname: str = "", **_):
        """设置用户昵称并持久化到 store"""
        if not self.permission_mgr:
            return Err(SdkError("NOT_INITIALIZED: 权限管理器未初始化"))

        uid_str = str(uid or "").strip()
        if not uid_str:
            return Err(SdkError("INVALID_ARGUMENT: uid 不能为空"))

        permission_level = self.permission_mgr.get_permission_level(uid_str)
        if permission_level == "none":
            return Err(SdkError(f"USER_NOT_FOUND: 用户 {uid_str} 不在信任列表中"))

        if permission_level == "admin":
            return Err(SdkError("ADMIN_NO_NICKNAME: 管理员始终被称为主人，无法设置昵称"))

        success = self.permission_mgr.set_nickname(uid_str, nickname)
        if not success:
            return Err(SdkError("SET_FAILED: 设置昵称失败"))

        await self._save_trusted_users()
        self.logger.info(f"已设置用户 {uid_str} 的昵称为: {nickname}")
        return Ok({"uid": uid_str, "nickname": nickname})

    @plugin_entry(
        id="list_trusted_users",
        name="列出信任用户",
        description="列出所有信任的 B站用户。",
        input_schema={"type": "object", "properties": {}},
    )
    async def list_trusted_users(self, **_):
        """列出所有信任用户"""
        if not self.permission_mgr:
            return Err(SdkError("NOT_INITIALIZED: 权限管理器未初始化"))

        users = self.permission_mgr.list_users()
        return Ok({"users": users, "count": len(users)})

    # ===== Message Processing =====

    async def _process_messages(self):
        """处理接收到的 B站私信"""
        while self._running:
            try:
                message = await self.bili_client.receive_message(timeout=1.0)
                if message:
                    task = asyncio.create_task(self._run_message_handler(message))
                    self._track_handler_task(task)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"处理消息时出错: {e}")
                await asyncio.sleep(1)

    async def _handle_message(self, message: Dict[str, Any]):
        """处理单条 B站私信"""
        sender_uid = message["sender_uid"]
        # bili_client 已通过 User.get_user_info() 获取真实 B站昵称
        bili_nickname = message.get("sender_nickname", sender_uid)
        content = message.get("content", "")
        content_type = message.get("content_type", "text")
        msg_kind = message.get("msg_kind", "text")

        # 检查权限
        if not self.permission_mgr.should_process(sender_uid):
            self.logger.debug(f"忽略来自 {sender_uid} 的消息（权限不足）")
            return

        permission_level = self.permission_mgr.get_permission_level(sender_uid)

        self.logger.info(
            f"收到 B站私信 [{msg_kind}] from {sender_uid} ({bili_nickname}), "
            f"权限: {permission_level}, 内容长度: {len(content)}"
        )

        # 构建消息文本
        if content_type == "image_url":
            # 下载图片转 base64（用于 AI 分析）
            b64_url = None
            if self.bili_client:
                b64_url = await self.bili_client.download_image_as_base64(content)
            message_text = "[用户发送了一张图片]"
            if b64_url:
                message_text = "[用户发送了一张图片，已下载为 base64]"
        elif msg_kind == "share_video":
            message_text = content  # 已经是格式化的视频信息
        else:
            message_text = content

        if not message_text.strip():
            return

        # 在消息文本前附加发送者信息，让 AI 知道是谁在说话
        sender_context = f"[来自 B站用户 {bili_nickname}（UID: {sender_uid}）的消息] "
        message_with_context = sender_context + message_text

        # 如果已有会话，更新 B站昵称缓存
        session_key = self._build_session_key(sender_uid)
        if session_key in self._user_sessions:
            user_data = self._user_sessions[session_key]
            old_nickname = user_data.get("bili_nickname", "")
            if bili_nickname and bili_nickname != sender_uid and bili_nickname != old_nickname:
                user_data["bili_nickname"] = bili_nickname
                self.logger.info(
                    f"更新用户 {sender_uid} 的 B站昵称: {old_nickname} -> {bili_nickname}"
                )

        # 生成 AI 回复
        reply_text = await self._generate_reply(
            message=message_with_context,
            permission_level=permission_level,
            sender_uid=sender_uid,
            user_nickname=bili_nickname,
        )

        if reply_text:
            try:
                await self.bili_client.send_text(sender_uid, reply_text)
                self.logger.info(
                    f"已回复 {sender_uid} ({bili_nickname}): {reply_text[:100]}"
                )
            except Exception as e:
                self.logger.error(f"发送回复给 {sender_uid} 失败: {e}")

    # ===== AI Conversation =====

    async def _generate_reply(
        self,
        message: str,
        permission_level: str,
        sender_uid: str,
        user_nickname: Optional[str] = None,
        persist_memory: Optional[bool] = None,
    ) -> Optional[str]:
        """生成 AI 回复内容"""
        if permission_level not in ("admin", "trusted"):
            return None

        try:
            from main_logic.omni_offline_client import OmniOfflineClient
            from utils.config_manager import get_config_manager

            config_manager = get_config_manager()

            # 获取角色数据
            master_name, her_name, _, catgirl_data, _, lanlan_prompt_map, _, _, _ = (
                config_manager.get_character_data()
            )

            # 确定用户称呼（优先级：配置自定义昵称 > B站真实昵称 > UID）
            custom_nickname = self.permission_mgr.get_nickname(sender_uid) if self.permission_mgr else None
            if permission_level == "admin":
                user_title = master_name if master_name else "主人"
            else:
                # 优先使用管理员在插件中设置的自定义昵称
                if custom_nickname:
                    user_title = custom_nickname
                # 其次使用通过 B站 User.get_user_info() 获取的真实昵称
                elif user_nickname and user_nickname != sender_uid:
                    user_title = user_nickname
                else:
                    user_title = f"B站用户{sender_uid}"

            # 获取角色配置
            current_character = catgirl_data.get(her_name, {})
            character_prompt = lanlan_prompt_map.get(her_name, "你是一个友好的AI助手")
            character_card_fields = {}
            for key, value in current_character.items():
                if key not in [
                    "_reserved", "voice_id", "system_prompt", "model_type",
                    "live2d", "vrm", "vrm_animation", "lighting", "vrm_rotation",
                    "live2d_item_id", "item_id", "idleAnimation",
                ]:
                    if isinstance(value, (str, int, float, bool)) and value:
                        character_card_fields[key] = value

            # 获取对话模型配置
            conversation_config = config_manager.get_model_api_config("conversation")
            base_url = conversation_config.get("base_url", "")
            api_key = conversation_config.get("api_key", "")
            model = conversation_config.get("model", "")

            should_use_memory = (permission_level == "admin")
            should_persist = should_use_memory if persist_memory is None else bool(persist_memory)

            # 会话管理
            session_key = self._build_session_key(sender_uid)

            if session_key not in self._user_sessions:
                self.logger.info(f"为 B站用户 {sender_uid} 创建新的 AI 会话")

                reply_chunks: list[str] = []

                async def on_text_delta(text: str, is_first: bool):
                    reply_chunks.append(text)

                user_session = OmniOfflineClient(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    on_text_delta=on_text_delta,
                )

                system_prompt = await self._build_session_instructions(
                    her_name=her_name,
                    master_name=master_name,
                    character_prompt=character_prompt,
                    character_card_fields=character_card_fields,
                    permission_level=permission_level,
                    sender_uid=sender_uid,
                    user_title=user_title,
                )

                await asyncio.wait_for(
                    user_session.connect(instructions=system_prompt),
                    timeout=self._ai_connect_timeout_seconds,
                )

                self._user_sessions[session_key] = {
                    "session": user_session,
                    "reply_chunks": reply_chunks,
                    "her_name": her_name,
                    "last_synced_index": 0,
                    "last_activity_at": time.time(),
                    "memory_enabled": should_persist,
                    "session_key": session_key,
                    "sender_uid": sender_uid,
                    "permission_level": permission_level,
                    "user_title": user_title,
                    "user_nickname": user_nickname,
                    "bili_nickname": user_nickname or "",  # 缓存 B站真实昵称
                    "lock": asyncio.Lock(),
                }

            user_data = self._user_sessions[session_key]
            user_session = user_data["session"]
            reply_chunks = user_data["reply_chunks"]
            user_data["last_activity_at"] = time.time()
            user_data.setdefault("lock", asyncio.Lock())

            async with user_data["lock"]:
                reply_chunks.clear()

                self.logger.info(f"发送消息到 AI (会话: {session_key}, 长度: {len(message)})")
                await asyncio.wait_for(
                    user_session.stream_text(message),
                    timeout=self._ai_turn_timeout_seconds,
                )

                completed = await self._wait_session_response_complete(user_session)
                if not completed:
                    self.logger.warning(f"会话 {session_key} 响应超时，关闭并丢弃该会话")
                    await user_session.close()
                    self._user_sessions.pop(session_key, None)
                    return None

                ai_reply = "".join(reply_chunks).strip()

            if ai_reply:
                # 记忆同步（可选）
                if user_data.get("memory_enabled"):
                    try:
                        count = await self._cache_session_delta(session_key, user_data)
                        if count:
                            self.logger.info(
                                f"[管理员] 成功同步 {count} 条消息到 Memory Server (会话: {session_key})"
                            )
                    except Exception as e:
                        self.logger.error(f"记忆同步失败: {e}")

                self.logger.info(f"AI 生成回复完成 (会话: {session_key}, 长度: {len(ai_reply)})")
                return ai_reply
            else:
                self.logger.warning("AI 未生成回复")
                return f"收到你的消息: {message}"

        except asyncio.TimeoutError:
            self.logger.warning(f"B站用户 {sender_uid} 会话处理超时")
            user_data = self._user_sessions.pop(session_key, None)
            session = user_data.get("session") if user_data else None
            if session:
                try:
                    await session.close()
                except Exception:
                    pass
            return None
        except Exception as e:
            self.logger.exception(f"AI 生成回复失败: {e}")
            return f"收到你的消息: {message}"

    async def _build_session_instructions(
        self,
        her_name: str,
        master_name: str,
        character_prompt: str,
        character_card_fields: dict,
        permission_level: str,
        sender_uid: str,
        user_title: str,
    ) -> str:
        """构建 AI 会话系统提示词"""
        from config.prompts_sys import SESSION_INIT_PROMPT
        from utils.language_utils import get_global_language

        try:
            from utils.i18n_utils import normalize_language_code
        except Exception:
            normalize_language_code = None

        user_language = get_global_language()
        short_language = (
            normalize_language_code(user_language, format="short")
            if normalize_language_code else user_language
        )

        init_prompt_template = SESSION_INIT_PROMPT.get(
            short_language,
            SESSION_INIT_PROMPT.get(user_language, SESSION_INIT_PROMPT["zh"]),
        )

        system_prompt_parts = [
            init_prompt_template.format(name=her_name),
            character_prompt,
        ]

        # 尝试加载记忆上下文
        if permission_level == "admin":
            try:
                import httpx
                from config import MEMORY_SERVER_PORT

                async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
                    response = await client.get(f"http://127.0.0.1:{MEMORY_SERVER_PORT}/new_dialog/{her_name}")
                    if response.is_success:
                        memory_context = response.text.strip()
                        if memory_context:
                            from config.prompts_sys import CONTEXT_SUMMARY_READY
                            context_ready_template = CONTEXT_SUMMARY_READY.get(
                                short_language,
                                CONTEXT_SUMMARY_READY.get(user_language, CONTEXT_SUMMARY_READY["zh"]),
                            )
                            system_prompt_parts.append(
                                memory_context + context_ready_template.format(
                                    name=her_name, master=master_name
                                )
                            )
            except Exception as e:
                self.logger.warning(f"读取 Memory Server 上下文失败: {e}")

        # 角色卡额外设定
        if character_card_fields:
            system_prompt_parts.append("\n======角色卡额外设定======")
            for field_name, field_value in character_card_fields.items():
                system_prompt_parts.append(f"{field_name}: {field_value}")
            system_prompt_parts.append("======角色卡设定结束======")

        # B站私聊环境说明
        friend_note = (
            f"- 当前对话对象是{master_name if master_name else '主人'}的朋友，不是主人本人\n"
            if permission_level != "admin" else ""
        )
        private_identity_target = (
            f"- 当前对话对象：{user_title}（B站UID: {sender_uid}），这是当前私聊对象\n"
            if permission_level != "admin" else
            f"- 当前对话对象：{user_title}（B站UID: {sender_uid}），这就是主人/管理员本人\n"
        )
        system_prompt_parts.append(f"""
======身份定义======
- 你自己：{her_name}，你是当前回复者
- 主人/管理员：{master_name if master_name else '主人'}，是固定身份
{private_identity_target}{friend_note}- 即使当前对话对象的名字、B站昵称、主人名字、你的名字或角色设定中的人物名称相同，也必须按上述身份定义区分，绝不能混淆角色
======身份定义结束======

======B站私聊环境======
- 你正在通过 B站私信与用户 {sender_uid} 对话
- 对方的称呼是：{user_title}
- 请保持角色设定，用简短自然的话回复（不超过50字）
- 不要使用 Markdown 格式，不要使用表情符号
- 记住你是 {her_name}，始终以 {her_name} 的身份回复
- 在回复中自然地称呼对方为\"{user_title}\"
- 注意不要重复之前的发言
======环境说明结束======""")

        system_prompt = "\n".join(system_prompt_parts)
        self.logger.info(f"系统提示词长度: {len(system_prompt)} 字符")
        return system_prompt

    # ===== Session Housekeeping =====

    async def _session_housekeeping_loop(self):
        """定期回收空闲会话"""
        try:
            while True:
                await asyncio.sleep(self.SESSION_SWEEP_INTERVAL_SECONDS)
                await self._flush_idle_sessions()
        except asyncio.CancelledError:
            raise

    async def _flush_idle_sessions(self):
        """回收空闲会话"""
        now = time.time()
        idle_sessions = []
        for session_key, user_data in list(self._user_sessions.items()):
            if not user_data.get("memory_enabled"):
                continue
            last_activity_at = user_data.get("last_activity_at") or now
            if now - last_activity_at >= self.SESSION_IDLE_TIMEOUT_SECONDS:
                idle_sessions.append(session_key)

        for session_key in idle_sessions:
            async def _finalize_if_still_idle() -> bool:
                current = self._user_sessions.get(session_key)
                if not current or not current.get("memory_enabled"):
                    return False
                current_last_activity = current.get("last_activity_at") or now
                if time.time() - current_last_activity < self.SESSION_IDLE_TIMEOUT_SECONDS:
                    return False
                return await self._finalize_session(session_key, reason="idle_timeout")

            session_lock = await self._get_session_lock(session_key)
            async with session_lock:
                await _finalize_if_still_idle()

    async def _flush_all_sessions(self, reason: str):
        """回收所有会话"""
        for session_key, user_data in list(self._user_sessions.items()):
            if not user_data.get("memory_enabled"):
                continue

            async def _finalize_existing() -> bool:
                current = self._user_sessions.get(session_key)
                if not current or not current.get("memory_enabled"):
                    return False
                return await self._finalize_session(session_key, reason=reason)

            session_lock = await self._get_session_lock(session_key)
            async with session_lock:
                await _finalize_existing()

    async def _finalize_session(self, session_key: str, reason: str) -> bool:
        """结算并关闭会话"""
        user_data = self._user_sessions.get(session_key)
        if not user_data or not user_data.get("memory_enabled"):
            return False

        session = user_data.get("session")
        her_name = user_data.get("her_name")
        if not session or not her_name:
            self._user_sessions.pop(session_key, None)
            return False

        try:
            conversation_history = getattr(session, "_conversation_history", []) or []
            last_synced_index = int(user_data.get("last_synced_index", 0))
            remaining_messages = self._conversation_slice_to_memory_messages(
                conversation_history, last_synced_index
            )

            if remaining_messages:
                result = await self._post_memory_history(
                    "process", her_name, remaining_messages, timeout=30.0
                )
                if result.get("status") == "error":
                    raise RuntimeError(result.get("message", "process failed"))
                self.logger.info(
                    f"[{reason}] 已为用户 {session_key} 完成记忆结算，消息数: {len(remaining_messages)}"
                )
            elif user_data.get("has_cached_memory"):
                settled_messages = self._conversation_slice_to_memory_messages(conversation_history, 0)
                result = await self._post_memory_history(
                    "settle", her_name, settled_messages, timeout=30.0
                )
                if result.get("status") == "error":
                    raise RuntimeError(result.get("message", "settle failed"))
                self.logger.info(f"[{reason}] 已为用户 {session_key} 完成缓存记忆结算")

            await session.close()
            self._user_sessions.pop(session_key, None)
            return True
        except Exception as e:
            self.logger.error(f"[{reason}] 用户 {session_key} 的记忆结算失败: {e}")
            return False

    def _conversation_slice_to_memory_messages(
        self, conversation_history: list, start_index: int = 0
    ) -> list[dict[str, Any]]:
        """将对话历史转换为记忆格式"""
        memory_messages = []
        for msg in conversation_history[start_index:]:
            msg_type = getattr(msg, "type", "")
            if msg_type not in ("human", "ai"):
                continue
            role = "user" if msg_type == "human" else "assistant"
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        parts.append(item)
                text = "".join(parts)
            else:
                text = str(content)
            if not text:
                continue
            memory_messages.append({
                "role": role,
                "content": [{"type": "text", "text": text}],
            })
        return memory_messages

    async def _cache_session_delta(
        self, session_key: str, user_data: dict[str, Any]
    ) -> int:
        """缓存会话增量到 Memory Server"""
        session = user_data.get("session")
        her_name = user_data.get("her_name")
        if not session or not her_name:
            return 0

        conversation_history = getattr(session, "_conversation_history", []) or []
        start_index = int(user_data.get("last_synced_index", 0))
        delta_messages = self._conversation_slice_to_memory_messages(
            conversation_history, start_index
        )
        if not delta_messages:
            return 0

        result = await self._post_memory_history(
            "cache", her_name, delta_messages, timeout=5.0
        )
        if result.get("status") == "error":
            raise RuntimeError(result.get("message", "cache failed"))

        user_data["last_synced_index"] = len(conversation_history)
        user_data["has_cached_memory"] = True
        return len(delta_messages)

    async def _post_memory_history(
        self, endpoint: str, her_name: str, messages: list[dict[str, Any]], timeout: float = 5.0
    ) -> dict[str, Any]:
        """发送对话历史到 Memory Server"""
        import httpx
        from config import MEMORY_SERVER_PORT

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://localhost:{MEMORY_SERVER_PORT}/{endpoint}/{her_name}",
                json={"input_history": json.dumps(messages, ensure_ascii=False)},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()

    # ===== Persistence =====

    async def _save_trusted_users(self) -> bool:
        """持久化信任用户列表到 store"""
        try:
            users = self.permission_mgr.list_users()
            await self.store.set("trusted_users", users)
            self.logger.info(f"成功持久化 {len(users)} 个信任用户到 store")
            return True
        except Exception as e:
            self.logger.error(f"持久化配置失败: {e}")
            return False
