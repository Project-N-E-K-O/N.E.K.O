
from __future__ import annotations

# 加载本地依赖
import sys as _sys, pathlib as _pathlib
_lib_dir = _pathlib.Path(__file__).parent / "lib"
if _lib_dir.exists() and str(_lib_dir) not in _sys.path:
    _sys.path.insert(0, str(_lib_dir))
del _sys, _pathlib, _lib_dir

import asyncio
import json
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from plugin.sdk.plugin import NekoPluginBase, lifecycle, neko_plugin, plugin_entry, Ok, Err, SdkError

from .qq_client import QQClient
from .permission import PermissionManager
from .group_permission import GroupPermissionManager



@neko_plugin
class QQAutoReplyPlugin(NekoPluginBase):
    SESSION_IDLE_TIMEOUT_SECONDS = 300
    SESSION_SWEEP_INTERVAL_SECONDS = 30

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        self.qq_client: Optional[QQClient] = None
        self.permission_mgr: Optional[PermissionManager] = None
        self.group_permission_mgr: Optional[GroupPermissionManager] = None

        self._running = False
        self._message_task: Optional[asyncio.Task] = None
        self._session_housekeeping_task: Optional[asyncio.Task] = None
        self._proactive_task: Optional[asyncio.Task] = None
        self._handler_tasks: set[asyncio.Task] = set()
        self._user_sessions: dict[str, dict[str, Any]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_locks_guard = asyncio.Lock()
        self._max_concurrent_messages = 3
        self._message_concurrency = asyncio.Semaphore(self._max_concurrent_messages)
        self._ai_connect_timeout_seconds = 10.0
        self._ai_turn_timeout_seconds = 60.0
        self._handler_shutdown_timeout_seconds = 10.0
        self._last_proactive_enabled = False
        self._last_proactive_send_at = 0.0
        self._last_proactive_greeting_at = 0.0

        # Normal 权限转述功能
        self._admin_qq: Optional[str] = None
        self._normal_relay_probability: float = 0.1
        self._truth_reply_probability: float = 0.1

        # NapCat 进程管理
        self._napcat_process: Optional[asyncio.subprocess.Process] = None
        self._napcat_show_window = False
        self._napcat_log_task: Optional[asyncio.Task] = None
        self._manages_napcat_process = False

    def _refresh_admin_qq(self) -> None:
        self._admin_qq = None
        if not self.permission_mgr:
            return
        for user in self.permission_mgr.list_users():
            if user.get("level") == "admin":
                qq = str(user.get("qq") or "").strip()
                if qq:
                    self._admin_qq = qq
                    return

    @staticmethod
    def _build_session_key(*, sender_id: str, is_group: bool, group_id: Optional[str] = None) -> str:
        sender = str(sender_id or "").strip()
        if is_group:
            return f"group:{str(group_id or '').strip()}:{sender}"
        return f"private:{sender}"

    def _message_session_key(self, message: Dict[str, Any]) -> Optional[str]:
        message_type = str(message.get("message_type") or "").strip()
        sender_id = str(message.get("user_id") or "").strip()
        if not sender_id:
            return None
        if message_type == "private":
            return self._build_session_key(sender_id=sender_id, is_group=False)
        if message_type == "group":
            group_id = str(message.get("group_id") or "").strip()
            if not group_id:
                return None
            return self._build_session_key(sender_id=sender_id, is_group=True, group_id=group_id)
        return None

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
            self.logger.error(f"Message handler task failed: {exc}")

    async def _run_message_handler(self, message: Dict[str, Any]) -> None:
        if not self._running:
            return
        session_key = self._message_session_key(message)
        async with self._message_concurrency:
            if session_key:
                session_lock = await self._get_session_lock(session_key)
                async with session_lock:
                    if not self._running:
                        return
                    await self._handle_message(message)
                return
            await self._handle_message(message)

    async def _run_with_session_lock(self, session_key: str, coro_factory) -> Any:
        session_lock = await self._get_session_lock(session_key)
        async with session_lock:
            return await coro_factory()

    async def _wait_session_response_complete(self, session: Any, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if not getattr(session, "_is_responding", False):
                return True
        return False

    @lifecycle(id="startup")
    async def startup(self, **_):
        """插件启动时初始化"""
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg
        qq_cfg = cfg.get("qq_auto_reply", {})

        # 初始化权限管理器（优先从 store 加载，回退到 TOML 配置）
        store_users_result = await self.store.get("trusted_users")
        if isinstance(store_users_result, Ok) and store_users_result.value is not None:
            trusted_users = store_users_result.value
            self.logger.info(f"从 store 加载 {len(trusted_users)} 个信任用户")
        else:
            trusted_users = qq_cfg.get("trusted_users", [])
        self.permission_mgr = PermissionManager(trusted_users)

        # 初始化群聊权限管理器（优先从 store 加载，回退到 TOML 配置）
        store_groups_result = await self.store.get("trusted_groups")
        if isinstance(store_groups_result, Ok) and store_groups_result.value is not None:
            trusted_groups = store_groups_result.value
            self.logger.info(f"从 store 加载 {len(trusted_groups)} 个信任群聊")
        else:
            trusted_groups = qq_cfg.get("trusted_groups", [])
        self.group_permission_mgr = GroupPermissionManager(trusted_groups)

        # 获取管理员 QQ（用于转述）
        self._refresh_admin_qq()

        # 获取转述概率
        self._normal_relay_probability = qq_cfg.get("normal_relay_probability", 0.1)
        self._truth_reply_probability = qq_cfg.get("truth_reply_probability", 0.1)
        self._max_concurrent_messages = max(1, int(qq_cfg.get("max_concurrent_messages", 3) or 3))
        self._message_concurrency = asyncio.Semaphore(self._max_concurrent_messages)
        self._ai_connect_timeout_seconds = max(1.0, float(qq_cfg.get("ai_connect_timeout_seconds", 10.0) or 10.0))
        self._ai_turn_timeout_seconds = max(5.0, float(qq_cfg.get("ai_turn_timeout_seconds", 60.0) or 60.0))
        self._handler_shutdown_timeout_seconds = max(1.0, float(qq_cfg.get("handler_shutdown_timeout_seconds", 10.0) or 10.0))

        # 初始化 QQ 客户端
        onebot_url = qq_cfg.get("onebot_url", "ws://127.0.0.1:3001")
        token = qq_cfg.get("token", "")
        self.qq_client = QQClient(onebot_url=onebot_url, token=token, logger=self.logger)
        self.logger.info(f"QQ 客户端已初始化: {onebot_url}")

        if not await self._start_napcat(show_window=True):
            self.logger.info(f"未找到内置 NapCat，使用外部 OneBot/NapCat 模式: {onebot_url}")

        if self._session_housekeeping_task is None or self._session_housekeeping_task.done():
            self._session_housekeeping_task = asyncio.create_task(self._session_housekeeping_loop())

        return Ok({"status": "running"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        """插件关闭时清理资源"""
        await self._stop_auto_reply_runtime(stop_napcat=True)
        await self._flush_all_memory_sessions(reason="shutdown")
        if self._session_housekeeping_task:
            self._session_housekeeping_task.cancel()
            try:
                await self._session_housekeeping_task
            except asyncio.CancelledError:
                pass
            self._session_housekeeping_task = None

        self.logger.info("QQ Auto Reply Plugin shutdown")
        return Ok({"status": "shutdown"})

    @plugin_entry(
        id="start_auto_reply",
        name="启动自动回复",
        description="开始监听 QQ 消息并自动回复。",
        input_schema={
            "type": "object",
            "properties": {},
        },
    )
    async def start_auto_reply(self, **_):
        """启动自动回复功能"""
        if self._running:
            return Ok({"status": "already_running"})

        if not self.qq_client:
            return Err(SdkError("NOT_INITIALIZED: QQ 客户端未初始化"))

        try:
            # 连接到 NapCat 服务
            if self._manages_napcat_process:
                self.logger.info("使用内置 NapCat 模式连接 OneBot 服务")
            else:
                self.logger.info(f"使用外部 OneBot/NapCat 模式连接: {self.qq_client.onebot_url}")
            await self.qq_client.connect()

            # 启动消息处理任务
            self._running = True
            self._message_task = asyncio.create_task(self._process_messages())
            enabled, _ = self._load_proactive_settings()
            if enabled:
                await self._send_startup_proactive_greeting()
            if self._proactive_task is None or self._proactive_task.done():
                self._proactive_task = asyncio.create_task(self._proactive_chat_loop())

            self.logger.info("Auto reply started")
            return Ok({"status": "started"})
        except Exception as e:
            self.logger.exception("Failed to start auto reply")
            if self._manages_napcat_process:
                return Err(SdkError(f"START_ERROR: 启动失败: {e}"))
            return Err(SdkError(
                f"START_ERROR: 无法连接到 OneBot 服务 {self.qq_client.onebot_url}，请先启动外部 NapCat/OneBot: {e}"
            ))

    @plugin_entry(
        id="stop_auto_reply",
        name="停止自动回复",
        description="停止监听 QQ 消息。",
        input_schema={
            "type": "object",
            "properties": {},
        },
    )
    async def stop_auto_reply(self, **_):
        """停止自动回复功能"""
        if not self._running and not self._message_task:
            return Ok({"status": "not_running"})

        await self._stop_auto_reply_runtime(stop_napcat=False)
        self.logger.info("Auto reply stopped")
        return Ok({"status": "stopped"})

    async def _stop_auto_reply_runtime(self, *, stop_napcat: bool):
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
                self.logger.warning(f"Timed out waiting for {len(handler_tasks)} message handler tasks to stop")
            self._handler_tasks.clear()

        if self._proactive_task:
            self._proactive_task.cancel()
            try:
                await self._proactive_task
            except asyncio.CancelledError:
                pass
            self._proactive_task = None

        if self.qq_client:
            await self.qq_client.disconnect()

        self._session_locks.clear()

        if stop_napcat:
            await self._stop_napcat()

    async def _process_messages(self):
        """处理接收到的 QQ 消息"""
        while self._running:
            try:
                message = await self.qq_client.receive_message()
                if message:
                    task = asyncio.create_task(self._run_message_handler(message))
                    self._track_handler_task(task)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error processing message: {e}")
                await asyncio.sleep(1)

    async def _handle_message(self, message: Dict[str, Any]):
        """处理单条消息，通过 OneBot API 回复"""
        message_type = message.get("message_type")
        sender_id = str(message.get("user_id") or "").strip()
        message_text = message.get("content", "")
        user_nickname = message.get("user_nickname")  # QQ 昵称

        if message_type == "private":
            session_key = self._build_session_key(sender_id=sender_id, is_group=False)
            if session_key in self._user_sessions:
                self._user_sessions[session_key]['last_activity_at'] = time.time()
            await self._handle_private_message(sender_id, message_text, user_nickname)

        elif message_type == "group":
            group_id = str(message.get("group_id") or "").strip()
            is_at_bot = message.get("is_at_bot", False)
            session_key = self._build_session_key(sender_id=sender_id, is_group=True, group_id=group_id)
            if session_key in self._user_sessions:
                self._user_sessions[session_key]['last_activity_at'] = time.time()
            await self._handle_group_message(group_id, sender_id, message_text, is_at_bot, user_nickname)

    async def _handle_private_message(self, sender_id: str, message_text: str, user_nickname: Optional[str] = None):
        """处理私聊消息"""
        # 检查权限
        permission_level = self.permission_mgr.get_permission_level(sender_id)
        if permission_level == "none":
            self.logger.debug(f"Ignored message from untrusted user: {sender_id}")
            return

        self.logger.info(
            f"Received private message from {sender_id} (level: {permission_level}, length: {len(message_text)})"
        )

        # Normal 权限：不直接回复，概率转述给管理员
        if permission_level == "normal":
            await self._handle_normal_relay(
                message_text,
                sender_id,
                source_type="private",
                source_id=sender_id
            )
            return

        # Admin 和 Trusted 权限：正常回复
        reply_text = await self._generate_reply(
            message_text, permission_level, sender_id,
            is_group=False,
            user_nickname=user_nickname
        )

        if reply_text:
            try:
                await self.qq_client.send_message(sender_id, reply_text)
                self.logger.info(f"Sent reply to {sender_id} (length: {len(reply_text)})")
            except Exception as e:
                self.logger.error(f"Failed to send message via OneBot: {e}")

    async def _handle_group_message(self, group_id: str, sender_id: str, message_text: str, is_at_bot: bool, user_nickname: Optional[str] = None):
        """处理群聊消息"""
        # 检查群聊权限
        group_level = self.group_permission_mgr.get_group_level(group_id)
        if group_level == "none":
            self.logger.debug(f"Ignored message from untrusted group: {group_id}")
            return

        self.logger.info(
            f"Received group message from group {group_id}, user {sender_id} (group level: {group_level}, length: {len(message_text)})"
        )

        # Normal 群聊：不响应 @，概率转述给管理员
        if group_level == "normal":
            await self._handle_normal_relay(
                message_text,
                sender_id,
                source_type="group",
                source_id=group_id
            )
            return

        # Trusted 群聊：只响应 @ 机器人的消息
        if group_level == "trusted":
            if not is_at_bot:
                self.logger.debug(f"Ignored group message without @: {group_id}")
                return
        elif group_level == "open":
            if not is_at_bot and random.random() >= self._truth_reply_probability:
                self.logger.debug(f"Skipped open group truth reply by probability: {group_id}")
                return
            self.logger.debug(f"Open group message allowed without @: {group_id}")

        # 生成回复（群聊中不检查用户权限）
        reply_text = await self._generate_reply(
            message_text, group_level, sender_id,
            is_group=True,
            group_id=group_id,
            user_nickname=user_nickname
        )

        if reply_text:
            try:
                await self.qq_client.send_group_message(group_id, reply_text)
                self.logger.info(f"Sent group reply to {group_id} (length: {len(reply_text)})")
            except Exception as e:
                self.logger.error(f"Failed to send group message via OneBot: {e}")

    @staticmethod
    def _sanitize_message_text(text: str) -> str:
        """将 CQ 码中的 at 替换为可读文本，避免 AI 误解"""
        import re
        # [CQ:at,qq=all] -> @全体成员
        text = re.sub(r'\[CQ:at,qq=all\]', '@全体成员', text)
        # [CQ:at,qq=12345] -> @用户12345
        text = re.sub(r'\[CQ:at,qq=(\d+)\]', r'@用户\1', text)
        return text

    async def _session_housekeeping_loop(self):
        """定期回收空闲 QQ 会话，并完成正式记忆结算。"""
        try:
            while True:
                await asyncio.sleep(self.SESSION_SWEEP_INTERVAL_SECONDS)
                await self._flush_idle_memory_sessions()
        except asyncio.CancelledError:
            raise

    def _load_proactive_settings(self) -> tuple[bool, float]:
        try:
            from utils.preferences import load_global_conversation_settings
            settings = load_global_conversation_settings() or {}
        except Exception as e:
            self.logger.warning(f"读取全局主动对话设置失败: {e}")
            settings = {}

        enabled = bool(settings.get('proactiveChatEnabled', False))
        raw_interval = settings.get('proactiveChatInterval', 30)
        try:
            interval_minutes = float(raw_interval)
        except (TypeError, ValueError):
            interval_minutes = 30.0
        interval_seconds = max(60.0, interval_minutes * 60.0)
        return enabled, interval_seconds

    async def _send_startup_proactive_greeting(self) -> bool:
        target = self._select_proactive_target()
        if not target:
            self.logger.info("主动问候跳过：未找到 admin QQ 目标")
            self._last_proactive_enabled = True
            return False

        sent = await self._send_proactive_greeting(target, force=True)
        self._last_proactive_enabled = True
        if sent:
            now = time.time()
            self._last_proactive_greeting_at = now
            self._last_proactive_send_at = now
        return sent

    async def _proactive_chat_loop(self):
        try:
            while True:
                enabled, interval_seconds = self._load_proactive_settings()
                if enabled:
                    await self._run_proactive_cycle(interval_seconds)
                else:
                    self._last_proactive_enabled = False
                await asyncio.sleep(min(interval_seconds, 30.0) if enabled else 30.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"主动对话循环异常: {e}")

    async def _run_proactive_cycle(self, interval_seconds: float):
        target = self._select_proactive_target()
        if not target:
            self._last_proactive_enabled = True
            return

        now = time.time()
        if not self._last_proactive_enabled:
            sent = await self._send_proactive_greeting(target)
            self._last_proactive_enabled = True
            if sent:
                self._last_proactive_greeting_at = now
                self._last_proactive_send_at = now
            return

        if now - self._last_proactive_send_at < interval_seconds:
            return

        sent = await self._send_proactive_turn(target)
        if sent:
            self._last_proactive_send_at = now

    def _select_proactive_target(self) -> Optional[dict[str, Any]]:
        admin_qq = str(self._admin_qq or '').strip()
        if not admin_qq:
            return None

        session_key = self._build_session_key(sender_id=admin_qq, is_group=False)
        user_data = self._user_sessions.get(session_key)
        if user_data:
            return user_data

        nickname = None
        if self.permission_mgr:
            nickname = self.permission_mgr.get_nickname(admin_qq)

        try:
            from utils.config_manager import get_config_manager

            config_manager = get_config_manager()
            master_name, _, _, _, _, _, _, _, _ = config_manager.get_character_data()
        except Exception:
            master_name = None

        return {
            'session_key': session_key,
            'sender_id': admin_qq,
            'permission_level': 'admin',
            'is_group': False,
            'group_id': None,
            'user_title': master_name if master_name else f'QQ用户{admin_qq}',
            'user_nickname': nickname,
            'memory_enabled': True,
        }

    def _build_proactive_context_summary(self, user_data: dict[str, Any]) -> str:
        sender_id = user_data.get('sender_id') or ''
        user_title = user_data.get('user_title') or f'QQ用户{sender_id}'
        session = user_data.get('session')
        history = getattr(session, '_conversation_history', []) if session else []
        recent_messages = self._conversation_slice_to_memory_messages(history, max(0, len(history) - 6))
        lines = [
            '当前是 QQ 私聊主动对话场景。',
            f'目标用户 QQ: {sender_id}',
            f'目标称呼: {user_title}',
        ]
        if recent_messages:
            lines.append('最近对话摘录：')
            for item in recent_messages[-6:]:
                role = '对方' if item.get('role') == 'user' else '你'
                text_parts = item.get('content') or []
                text = ''.join(part.get('text', '') for part in text_parts if isinstance(part, dict)).strip()
                if text:
                    lines.append(f'- {role}: {text[:120]}')
        else:
            lines.append('最近没有新的QQ对话摘录，可自然开启话题。')
        return '\n'.join(lines)

    async def _fetch_memory_context(self, her_name: str) -> str:
        try:
            import httpx
            from config import MEMORY_SERVER_PORT

            async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{MEMORY_SERVER_PORT}/new_dialog/{her_name}")
                if response.is_success:
                    return response.text.strip()
                self.logger.warning(f"主动对话读取 Memory Server 上下文失败: {response.status_code}")
        except Exception as e:
            self.logger.warning(f"主动对话读取 Memory Server 上下文失败: {e}")
        return ''

    async def _fetch_last_conversation_gap(self, her_name: str) -> Optional[float]:
        try:
            import httpx
            from config import MEMORY_SERVER_PORT

            async with httpx.AsyncClient(timeout=3.0, proxy=None, trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{MEMORY_SERVER_PORT}/last_conversation_gap/{her_name}")
                response.raise_for_status()
                data = response.json()
            gap = data.get('gap_seconds')
            return float(gap) if gap is not None else None
        except Exception as e:
            self.logger.warning(f"读取上次对话间隔失败: {e}")
            return None

    def _format_elapsed_gap(self, gap_seconds: float) -> str:
        if gap_seconds < 3600:
            minutes = max(1, int(gap_seconds // 60))
            return f"{minutes}分钟"
        if gap_seconds < 86400:
            hours = max(1, int(gap_seconds // 3600))
            return f"{hours}小时"
        days = max(1, int(gap_seconds // 86400))
        return f"{days}天"

    async def _ensure_session_for_user(self, user_data: dict[str, Any]) -> Optional[dict[str, Any]]:
        session_key = user_data.get('session_key')
        if not session_key:
            return None

        existing = self._user_sessions.get(session_key)
        if existing:
            if 'lock' not in existing:
                existing['lock'] = asyncio.Lock()
            if not existing.get('sender_id'):
                existing['sender_id'] = user_data.get('sender_id')
            if 'is_group' not in existing:
                existing['is_group'] = bool(user_data.get('is_group'))
            if 'group_id' not in existing:
                existing['group_id'] = user_data.get('group_id')
            if not existing.get('user_title'):
                existing['user_title'] = user_data.get('user_title') or f"QQ用户{user_data.get('sender_id') or ''}"
            if 'permission_level' not in existing:
                existing['permission_level'] = user_data.get('permission_level')
            return existing

        try:
            from main_logic.omni_offline_client import OmniOfflineClient
            from utils.config_manager import get_config_manager

            config_manager = get_config_manager()
            master_name, her_name, _, catgirl_data, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()
            current_character = catgirl_data.get(her_name, {})
            character_prompt = lanlan_prompt_map.get(her_name, "你是一个友好的AI助手")
            character_card_fields = {}
            for key, value in current_character.items():
                if key not in ['_reserved', 'voice_id', 'system_prompt', 'model_type',
                               'live2d', 'vrm', 'vrm_animation', 'lighting', 'vrm_rotation',
                               'live2d_item_id', 'item_id', 'idleAnimation']:
                    if isinstance(value, (str, int, float, bool)) and value:
                        character_card_fields[key] = value

            conversation_config = config_manager.get_model_api_config('conversation')
            base_url = conversation_config.get('base_url', '')
            api_key = conversation_config.get('api_key', '')
            model = conversation_config.get('model', '')

            reply_chunks = []

            async def on_text_delta(text: str, is_first: bool):
                reply_chunks.append(text)

            user_session = OmniOfflineClient(
                base_url=base_url,
                api_key=api_key,
                model=model,
                on_text_delta=on_text_delta
            )

            system_prompt, memory_enabled = await self._build_qq_session_instructions(
                her_name=her_name,
                master_name=master_name,
                character_prompt=character_prompt,
                character_card_fields=character_card_fields,
                permission_level=str(user_data.get('permission_level') or 'trusted'),
                sender_id=str(user_data.get('sender_id') or ''),
                user_title=str(user_data.get('user_title') or f"QQ用户{user_data.get('sender_id') or ''}"),
                is_group=bool(user_data.get('is_group')),
                group_id=user_data.get('group_id'),
            )
            await asyncio.wait_for(
                user_session.connect(instructions=system_prompt),
                timeout=self._ai_connect_timeout_seconds,
            )

            created = {
                'session': user_session,
                'reply_chunks': reply_chunks,
                'her_name': her_name,
                'character_fields': character_card_fields,
                'last_synced_index': 0,
                'last_activity_at': time.time(),
                'memory_enabled': memory_enabled,
                'has_cached_memory': False,
                'session_key': session_key,
                'sender_id': str(user_data.get('sender_id') or ''),
                'permission_level': str(user_data.get('permission_level') or 'trusted'),
                'is_group': bool(user_data.get('is_group')),
                'group_id': user_data.get('group_id'),
                'user_title': str(user_data.get('user_title') or f"QQ用户{user_data.get('sender_id') or ''}"),
                'user_nickname': user_data.get('user_nickname'),
                'lock': asyncio.Lock(),
                'last_proactive_at': 0.0,
            }
            self._user_sessions[session_key] = created
            return created
        except Exception as e:
            self.logger.error(f"创建主动对话会话失败: {e}")
            return None

    async def _send_proactive_greeting(self, target: dict[str, Any], force: bool = False) -> bool:
        session_data = await self._ensure_session_for_user(target)
        if not session_data:
            return False

        gap_seconds = await self._fetch_last_conversation_gap(session_data['her_name'])
        if gap_seconds is None:
            gap_seconds = max(time.time() - float(session_data.get('last_activity_at') or 0.0), 0.0)

        try:
            from config.prompts_proactive import get_greeting_prompt, get_time_of_day_hint
            from utils.language_utils import get_global_language
        except Exception as e:
            self.logger.error(f"加载主动问候提示词失败: {e}")
            return False

        lang = get_global_language()
        template = get_greeting_prompt(gap_seconds, lang)
        if not template and force:
            from config.prompts_proactive import GREETING_PROMPT_SHORT
            lang_key = lang if lang in GREETING_PROMPT_SHORT else 'zh'
            template = GREETING_PROMPT_SHORT.get(lang_key, GREETING_PROMPT_SHORT['zh'])
        if not template:
            self.logger.info("主动问候条件未满足，跳过发送")
            return False

        elapsed = self._format_elapsed_gap(gap_seconds)
        instruction = template.format(
            elapsed=elapsed,
            name=session_data['her_name'],
            master=session_data.get('user_title') or f"QQ用户{session_data.get('sender_id') or ''}",
            time_hint=get_time_of_day_hint(lang),
            holiday_hint='',
        ) + "\n======QQ发送约束======\n- 这是通过QQ私聊发送的消息\n- 只输出最终要发送给对方的话\n- 不要使用Markdown、表情符号或多段长文\n- 如果不适合发送，输出 [PASS]\n======约束结束======"

        return await self._deliver_proactive_instruction(session_data, instruction)

    async def _send_proactive_turn(self, target: dict[str, Any]) -> bool:
        session_data = await self._ensure_session_for_user(target)
        if not session_data:
            return False

        try:
            from config.prompts_proactive import get_proactive_chat_prompt
            from utils.language_utils import get_global_language
        except Exception as e:
            self.logger.error(f"加载主动对话提示词失败: {e}")
            return False

        lang = get_global_language()
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        master_name, her_name, _, _, _, _, _, _, _ = config_manager.get_character_data()
        prompt_template = get_proactive_chat_prompt('home', lang)
        memory_context = await self._fetch_memory_context(session_data['her_name'])
        trending_content = self._build_proactive_context_summary(session_data)
        current_target = session_data.get('user_title') or f"QQ用户{session_data.get('sender_id') or ''}"
        instruction = prompt_template.format(
            lanlan_name=her_name,
            master_name=session_data.get('user_title') or master_name or f"QQ用户{session_data.get('sender_id') or ''}",
            memory_context=memory_context or '（暂无新的记忆补充）',
            trending_content=trending_content,
        ) + f"\n======QQ主动私聊约束======\n- 当前对象: {current_target}（QQ: {session_data.get('sender_id') or ''}）\n- 这是没有新用户输入时的主动搭话\n- 只输出最终要发送的一小段自然中文，尽量简短\n- 不要使用Markdown、表情符号，不要假装看到了QQ界面之外的信息\n- 如果现在不适合主动发言，输出 [PASS]\n======约束结束======"

        return await self._deliver_proactive_instruction(session_data, instruction)

    async def _deliver_proactive_instruction(self, session_data: dict[str, Any], instruction: str) -> bool:
        if not self.qq_client:
            return False

        lock = session_data.setdefault('lock', asyncio.Lock())
        session = session_data.get('session')
        if not session:
            return False

        async with lock:
            session_data['last_activity_at'] = time.time()
            reply_chunks = session_data.get('reply_chunks') or []
            reply_chunks.clear()
            ok = await session.prompt_ephemeral(instruction)
            if not ok:
                return False
            proactive_text = ''.join(reply_chunks).strip()
            if not proactive_text or proactive_text == '[PASS]':
                self.logger.info(f"主动对话未发送内容 (会话: {session_data.get('session_key')})")
                return False

            if session_data.get('is_group') and session_data.get('group_id'):
                await self.qq_client.send_group_message(str(session_data['group_id']), proactive_text)
            else:
                await self.qq_client.send_message(str(session_data.get('sender_id') or ''), proactive_text)
            session_data['last_proactive_at'] = time.time()
            self.logger.info(f"主动消息发送成功 (会话: {session_data.get('session_key')}, length: {len(proactive_text)})")
            return True

    async def _flush_idle_memory_sessions(self):
        now = time.time()
        idle_sessions = []
        for session_key, user_data in list(self._user_sessions.items()):
            if not user_data.get('memory_enabled'):
                continue
            last_activity_at = user_data.get('last_activity_at') or now
            if now - last_activity_at >= self.SESSION_IDLE_TIMEOUT_SECONDS:
                idle_sessions.append(session_key)

        for session_key in idle_sessions:
            async def _finalize_if_still_idle() -> bool:
                current = self._user_sessions.get(session_key)
                if not current or not current.get('memory_enabled'):
                    return False
                current_last_activity = current.get('last_activity_at') or now
                if time.time() - current_last_activity < self.SESSION_IDLE_TIMEOUT_SECONDS:
                    return False
                return await self._finalize_user_memory_session(session_key, reason="idle_timeout")

            await self._run_with_session_lock(session_key, _finalize_if_still_idle)

    async def _flush_all_memory_sessions(self, reason: str):
        for session_key, user_data in list(self._user_sessions.items()):
            if not user_data.get('memory_enabled'):
                continue

            async def _finalize_existing() -> bool:
                current = self._user_sessions.get(session_key)
                if not current or not current.get('memory_enabled'):
                    return False
                return await self._finalize_user_memory_session(session_key, reason=reason)

            await self._run_with_session_lock(session_key, _finalize_existing)

    def _conversation_slice_to_memory_messages(self, conversation_history: list, start_index: int = 0) -> list[dict[str, Any]]:
        memory_messages = []
        for msg in conversation_history[start_index:]:
            msg_type = getattr(msg, 'type', '')
            if msg_type not in ('human', 'ai'):
                continue
            role = 'user' if msg_type == 'human' else 'assistant'
            content = getattr(msg, 'content', '')
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        parts.append(item.get('text', ''))
                    elif isinstance(item, str):
                        parts.append(item)
                text = ''.join(parts)
            else:
                text = str(content)
            if not text:
                continue
            memory_messages.append({
                'role': role,
                'content': [{'type': 'text', 'text': text}]
            })
        return memory_messages

    async def _post_memory_history(self, endpoint: str, her_name: str, messages: list[dict[str, Any]], timeout: float = 5.0) -> dict[str, Any]:
        import httpx
        from config import MEMORY_SERVER_PORT

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://localhost:{MEMORY_SERVER_PORT}/{endpoint}/{her_name}",
                json={'input_history': json.dumps(messages, ensure_ascii=False)},
                timeout=timeout
            )
            response.raise_for_status()
            return response.json()

    async def _cache_session_delta(self, session_key: str, user_data: dict[str, Any]) -> int:
        session = user_data.get('session')
        her_name = user_data.get('her_name')
        if not session or not her_name:
            return 0
        conversation_history = getattr(session, '_conversation_history', []) or []
        start_index = int(user_data.get('last_synced_index', 0))
        delta_messages = self._conversation_slice_to_memory_messages(conversation_history, start_index)
        if not delta_messages:
            return 0
        result = await self._post_memory_history('cache', her_name, delta_messages, timeout=5.0)
        if result.get('status') == 'error':
            raise RuntimeError(result.get('message', 'cache failed'))
        user_data['last_synced_index'] = len(conversation_history)
        user_data['has_cached_memory'] = True
        return len(delta_messages)

    async def _finalize_user_memory_session(self, session_key: str, reason: str) -> bool:
        user_data = self._user_sessions.get(session_key)
        if not user_data or not user_data.get('memory_enabled'):
            return False

        session = user_data.get('session')
        her_name = user_data.get('her_name')
        if not session or not her_name:
            self._user_sessions.pop(session_key, None)
            return False

        try:
            conversation_history = getattr(session, '_conversation_history', []) or []
            last_synced_index = int(user_data.get('last_synced_index', 0))
            remaining_messages = self._conversation_slice_to_memory_messages(conversation_history, last_synced_index)

            if remaining_messages:
                result = await self._post_memory_history('process', her_name, remaining_messages, timeout=30.0)
                if result.get('status') == 'error':
                    raise RuntimeError(result.get('message', 'process failed'))
                self.logger.info(f"[{reason}] 已为用户 {session_key} 完成正式记忆结算，消息数: {len(remaining_messages)}")
            elif user_data.get('has_cached_memory'):
                settled_messages = self._conversation_slice_to_memory_messages(conversation_history, 0)
                result = await self._post_memory_history('settle', her_name, settled_messages, timeout=30.0)
                if result.get('status') == 'error':
                    raise RuntimeError(result.get('message', 'settle failed'))
                self.logger.info(f"[{reason}] 已为用户 {session_key} 完成缓存记忆结算")

            await session.close()
            self._user_sessions.pop(session_key, None)
            return True
        except Exception as e:
            self.logger.error(f"[{reason}] 用户 {session_key} 的记忆结算失败: {e}")
            return False


    async def _handle_normal_relay(self, message_text: str, sender_id: str, source_type: str, source_id: str):
        """处理 Normal 权限的转述逻辑"""
        # 清理 CQ 码，避免 AI 误解 @ 对象
        message_text = self._sanitize_message_text(message_text)

        # 检查是否有管理员
        if not self._admin_qq:
            self.logger.debug("No admin QQ configured, skipping relay")
            return

        # 概率触发
        if random.random() > self._normal_relay_probability:
            self.logger.debug(f"Relay not triggered (probability: {self._normal_relay_probability})")
            return

        self.logger.info(f"Relay triggered for {source_type} {source_id}, user {sender_id}")

        # 生成转述给主人的回复
        temp_session = None
        try:
            from main_logic.omni_offline_client import OmniOfflineClient
            from utils.config_manager import get_config_manager
            from config.prompts_sys import SESSION_INIT_PROMPT
            from utils.language_utils import get_global_language

            config_manager = get_config_manager()
            master_name, her_name, _, catgirl_data, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()

            # 获取角色核心提示词
            character_prompt = lanlan_prompt_map.get(her_name, "你是一个友好的AI助手")

            # 获取角色卡额外字段
            current_character = catgirl_data.get(her_name, {})
            character_card_fields = {}
            for key, value in current_character.items():
                if key not in ['_reserved', 'voice_id', 'system_prompt', 'model_type',
                               'live2d', 'vrm', 'vrm_animation', 'lighting', 'vrm_rotation',
                               'live2d_item_id', 'item_id', 'idleAnimation']:
                    if isinstance(value, (str, int, float, bool)) and value:
                        character_card_fields[key] = value

            # 获取对话模型配置
            conversation_config = config_manager.get_model_api_config('conversation')
            base_url = conversation_config.get('base_url', '')
            api_key = conversation_config.get('api_key', '')
            model = conversation_config.get('model', '')

            # 创建临时会话
            reply_chunks = []

            async def on_text_delta(text: str, is_first: bool):
                reply_chunks.append(text)

            temp_session = OmniOfflineClient(
                base_url=base_url,
                api_key=api_key,
                model=model,
                on_text_delta=on_text_delta
            )

            # 构建转述提示词
            user_language = get_global_language()
            init_prompt = SESSION_INIT_PROMPT.get(user_language, SESSION_INIT_PROMPT['zh'])
            init_prompt = init_prompt.format(name=her_name)

            system_prompt_parts = [
                init_prompt,
                character_prompt
            ]

            # TODO: i18n — 角色卡分隔标记、转述场景、relay_prompt 需国际化
            if character_card_fields:
                system_prompt_parts.append("\n======角色卡额外设定======")
                for field_name, field_value in character_card_fields.items():
                    system_prompt_parts.append(f"{field_name}: {field_value}")
                system_prompt_parts.append("======角色卡设定结束======")

            # 转述场景说明
            source_desc = f"QQ 群 {source_id}" if source_type == "group" else f"QQ 用户 {source_id}"
            system_prompt_parts.append(f"""
======转述场景======
- 你在 {source_desc} 中看到了用户 {sender_id} 的发言
- 发言内容："{message_text}"
- 现在你要把这个有趣的内容转述给{master_name if master_name else "主人"}
- 请用简短自然的话（不超过50字）告诉{master_name if master_name else "主人"}这件事
- 不要使用 Markdown 格式，不要使用表情符号
- 记住你是 {her_name}，以 {her_name} 的身份转述
======场景说明结束======""")

            system_prompt = "\n".join(system_prompt_parts)

            await asyncio.wait_for(
                temp_session.connect(instructions=system_prompt),
                timeout=self._ai_connect_timeout_seconds,
            )

            # 发送转述请求
            relay_prompt = f"请把这个内容转述给{master_name if master_name else '主人'}：{message_text}"
            await asyncio.wait_for(
                temp_session.stream_text(relay_prompt),
                timeout=self._ai_turn_timeout_seconds,
            )

            completed = await self._wait_session_response_complete(temp_session)
            if not completed:
                self.logger.warning("Relay generation timed out; closing temporary session")
                return

            relay_text = ''.join(reply_chunks).strip()

            if relay_text:
                # 发送给管理员
                try:
                    await self.qq_client.send_message(self._admin_qq, relay_text)
                    self.logger.info(f"Relayed to admin {self._admin_qq} (length: {len(relay_text)})")
                except Exception as e:
                    self.logger.error(f"Failed to relay to admin: {e}")

        except asyncio.TimeoutError:
            self.logger.warning(f"Relay generation timed out for {source_type} {source_id}, user {sender_id}")
        except Exception as e:
            self.logger.error(f"Failed to generate relay message: {e}")
        finally:
            if temp_session:
                try:
                    await temp_session.close()
                except Exception as close_error:
                    self.logger.warning(f"Failed to close temporary relay session: {close_error}")


    async def _build_qq_session_instructions(
        self,
        her_name: str,
        master_name: str,
        character_prompt: str,
        character_card_fields: dict,
        permission_level: str,
        sender_id: str,
        user_title: str,
        is_group: bool = False,
        group_id: Optional[str] = None,
        use_memory_context: Optional[bool] = None,
        address_user_by_name: bool = True,
        group_facing: bool = False,
    ) -> tuple[str, bool]:
        """构建 QQ 会话初始化提示词，复用 N.E.K.O 当前提示词链语义。"""
        from config.prompts_sys import CONTEXT_SUMMARY_READY, SESSION_INIT_PROMPT
        from utils.language_utils import get_global_language

        try:
            from utils.i18n_utils import normalize_language_code
        except Exception:
            normalize_language_code = None

        user_language = get_global_language()
        short_language = (
            normalize_language_code(user_language, format='short')
            if normalize_language_code else user_language
        )

        init_prompt_template = SESSION_INIT_PROMPT.get(
            short_language,
            SESSION_INIT_PROMPT.get(user_language, SESSION_INIT_PROMPT['zh'])
        )
        context_ready_template = CONTEXT_SUMMARY_READY.get(
            short_language,
            CONTEXT_SUMMARY_READY.get(user_language, CONTEXT_SUMMARY_READY['zh'])
        )

        system_prompt_parts = [
            init_prompt_template.format(name=her_name),
            character_prompt,
        ]

        should_use_memory_context = (
            (not is_group and permission_level == "admin")
            if use_memory_context is None else bool(use_memory_context)
        )
        if should_use_memory_context:
            try:
                import httpx
                from config import MEMORY_SERVER_PORT

                async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
                    response = await client.get(f"http://127.0.0.1:{MEMORY_SERVER_PORT}/new_dialog/{her_name}")
                    if response.is_success:
                        memory_context = response.text.strip()
                        if memory_context:
                            system_prompt_parts.append(
                                memory_context + context_ready_template.format(name=her_name, master=master_name)
                            )
                    else:
                        self.logger.warning(f"读取 Memory Server 上下文失败: {response.status_code}")
            except Exception as e:
                self.logger.warning(f"读取 Memory Server 上下文失败: {e}")

        # TODO: i18n — 角色卡分隔标记、群聊/私聊环境说明需国际化
        if character_card_fields:
            system_prompt_parts.append("\n======角色卡额外设定======")
            for field_name, field_value in character_card_fields.items():
                system_prompt_parts.append(f"{field_name}: {field_value}")
            system_prompt_parts.append("======角色卡设定结束======")

        if is_group:
            if group_facing:
                system_prompt_parts.append(f"""
======身份定义======
- 你自己：{her_name}，你是当前回复者
- 主人/管理员：{master_name if master_name else '主人'}，是固定身份，不等于群内任意成员
- 当前发言场景：QQ群 {group_id} 的群发消息，面向整个群体
- 当前消息对象是群内成员整体，不是某一个单独用户
- 即使群号、QQ号、用户昵称、主人名字、你的名字或角色设定中的人物名称相同，也必须按上述身份定义区分，绝不能混淆角色
======身份定义结束======

======QQ 群聊环境======
- 你正在 QQ 群 {group_id} 中向群内成员发言
- 这是群聊环境，有多个用户在场
- 这次回复应面向整个群体，而不是某个单独用户
- 默认使用“大家”“各位”“群友们”等集体称呼
- 不要把群号、QQ号或单个用户当成人名来称呼
- 除非消息内容明确需要，否则不要点名某个具体用户
- 请保持角色设定，用简短自然的话回复（不超过50字）
- 不要使用 Markdown 格式，不要使用表情符号
- 记住你是 {her_name}，始终以 {her_name} 的身份回复
- 注意不要重复之前的发言
======环境说明结束======""")
            else:
                naming_instruction = (
                    f'- 在回复中自然地称呼对方为"{user_title}"'
                    if address_user_by_name else
                    '- 不要直接称呼对方名字、昵称或QQ号，只针对当前话题自然回应'
                )
                title_line = f"- 当前发言人的称呼是：{user_title}\n" if address_user_by_name else ""
                system_prompt_parts.append(f"""
======身份定义======
- 你自己：{her_name}，你是当前回复者
- 主人/管理员：{master_name if master_name else '主人'}，是固定身份，不等于当前发言人
- 当前发言人：{user_title}（QQ: {sender_id}），是本轮群聊中正在对话的对象
- 当前发言人不是你自己，也不是主人/管理员，除非系统另有明确说明
- 即使当前发言人的名字、QQ昵称、主人名字、你的名字或角色设定中的人物名称相同，也必须按上述身份定义区分，绝不能混淆角色
======身份定义结束======

======QQ 群聊环境======
- 你正在 QQ 群 {group_id} 中与用户 {sender_id} 对话
{title_line}- 这是群聊环境，有多个用户在场
- 请保持角色设定，用简短自然的话回复（不超过50字）
- 不要使用 Markdown 格式，不要使用表情符号
- 记住你是 {her_name}，始终以 {her_name} 的身份回复
{naming_instruction}
- 注意不要重复之前的发言
======环境说明结束======""")
        else:
            friend_note = (
                f"- 当前对话对象是{master_name if master_name else '主人'}的朋友，不是主人本人\n"
                if permission_level != "admin" else ""
            )
            private_identity_target = (
                f"- 当前对话对象：{user_title}（QQ: {sender_id}），这是当前私聊对象\n"
                if permission_level != "admin" else
                f"- 当前对话对象：{user_title}（QQ: {sender_id}），这就是主人/管理员本人\n"
            )
            system_prompt_parts.append(f"""
======身份定义======
- 你自己：{her_name}，你是当前回复者
- 主人/管理员：{master_name if master_name else '主人'}，是固定身份
{private_identity_target}{friend_note}- 即使当前对话对象的名字、QQ昵称、主人名字、你的名字或角色设定中的人物名称相同，也必须按上述身份定义区分，绝不能混淆角色
======身份定义结束======

======QQ 私聊环境======
- 你正在通过 QQ 与用户 {sender_id} 私聊
- 对方的称呼是：{user_title}
- 请保持角色设定，用简短自然的话回复（不超过50字）
- 不要使用 Markdown 格式，不要使用表情符号
- 记住你是 {her_name}，始终以 {her_name} 的身份回复
- 在回复中自然地称呼对方为\"{user_title}\"
- 注意不要重复之前的发言
======环境说明结束======""")

        system_prompt = "\n".join(system_prompt_parts)
        self.logger.info(f"系统提示词长度: {len(system_prompt)} 字符")
        self.logger.info(f"使用语言: {user_language}, 初始提示: {init_prompt_template[:50]}...")
        return system_prompt, should_use_memory_context


    async def _generate_reply(
        self, message: str, permission_level: str, sender_id: str,
        is_group: bool = False, group_id: str = None, user_nickname: Optional[str] = None,
        use_memory_context: Optional[bool] = None, persist_memory: Optional[bool] = None,
        ephemeral_session: bool = False, group_facing: bool = False,
    ) -> Optional[str]:
        """生成回复内容（使用 OmniOfflineClient + 可选 Memory Server 同步）"""
        # 私聊：只为 admin 和 trusted 用户生成 AI 回复
        # 群聊：所有 @ 机器人的用户都生成回复
        if not is_group and permission_level not in ["admin", "trusted"]:
            return None

        try:
            from main_logic.omni_offline_client import OmniOfflineClient
            from utils.config_manager import get_config_manager

            config_manager = get_config_manager()

            # 获取角色完整数据
            master_name, her_name, _, catgirl_data, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()

            # 获取用户称呼
            # 1. 优先使用插件设置的昵称
            custom_nickname = self.permission_mgr.get_nickname(sender_id)

            # 2. 根据场景确定称呼
            if is_group:
                # 群聊中：自定义昵称 > QQ昵称 > QQ号
                if custom_nickname:
                    user_title = custom_nickname
                elif user_nickname:
                    user_title = user_nickname
                else:
                    user_title = f"QQ用户{sender_id}"
            else:
                # 私聊中：根据权限等级确定称呼
                if permission_level == "admin":
                    # 管理员：使用 master_name
                    user_title = master_name if master_name else "主人"
                else:
                    # 其他用户：自定义昵称 > QQ昵称 > QQ号
                    if custom_nickname:
                        user_title = custom_nickname
                    elif user_nickname:
                        user_title = user_nickname
                    else:
                        user_title = f"QQ用户{sender_id}"

            # 获取当前角色的完整配置
            current_character = catgirl_data.get(her_name, {})

            # 获取角色核心提示词（system_prompt）
            # TODO: i18n — fallback prompt 需国际化
            character_prompt = lanlan_prompt_map.get(her_name, "你是一个友好的AI助手")

            # 获取角色卡的额外字段（如果有）
            character_card_fields = {}
            for key, value in current_character.items():
                # 排除系统保留字段
                if key not in ['_reserved', 'voice_id', 'system_prompt', 'model_type',
                               'live2d', 'vrm', 'vrm_animation', 'lighting', 'vrm_rotation',
                               'live2d_item_id', 'item_id', 'idleAnimation']:
                    if isinstance(value, (str, int, float, bool)) and value:
                        character_card_fields[key] = value

            self.logger.info(f"使用角色: {her_name}, 额外字段: {list(character_card_fields.keys())}")

            # 获取对话模型配置
            conversation_config = config_manager.get_model_api_config('conversation')
            base_url = conversation_config.get('base_url', '')
            api_key = conversation_config.get('api_key', '')
            model = conversation_config.get('model', '')

            should_use_memory_context = (
                (not is_group and permission_level == "admin")
                if use_memory_context is None else bool(use_memory_context)
            )
            should_persist_memory = (
                should_use_memory_context
                if persist_memory is None else bool(persist_memory)
            )

            # 为每个 QQ 用户维护独立的对话客户端
            if not hasattr(self, '_user_sessions'):
                self._user_sessions = {}

            session_key = self._build_session_key(sender_id=sender_id, is_group=is_group, group_id=group_id)
            if ephemeral_session:
                session_key = f"{session_key}:ephemeral:{time.time_ns()}"

            if session_key not in self._user_sessions:
                self.logger.info(f"为会话 {session_key} 创建新的对话 session")

                # 创建回复收集器
                reply_chunks = []

                async def on_text_delta(text: str, is_first: bool):
                    reply_chunks.append(text)

                # 创建用户专属 OmniOfflineClient
                user_session = OmniOfflineClient(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    on_text_delta=on_text_delta
                )

                system_prompt, memory_context_used = await self._build_qq_session_instructions(
                    her_name=her_name,
                    master_name=master_name,
                    character_prompt=character_prompt,
                    character_card_fields=character_card_fields,
                    permission_level=permission_level,
                    sender_id=sender_id,
                    user_title=user_title,
                    is_group=is_group,
                    group_id=group_id,
                    use_memory_context=should_use_memory_context,
                    address_user_by_name=not (is_group and permission_level == "open"),
                    group_facing=group_facing,
                )

                await asyncio.wait_for(
                user_session.connect(instructions=system_prompt),
                timeout=self._ai_connect_timeout_seconds,
            )

                self._user_sessions[session_key] = {
                    'session': user_session,
                    'reply_chunks': reply_chunks,
                    'her_name': her_name,
                    'character_fields': character_card_fields,
                    'last_synced_index': 0,
                    'last_activity_at': time.time(),
                    'memory_enabled': should_persist_memory,
                    'memory_context_used': memory_context_used,
                    'has_cached_memory': False,
                    'session_key': session_key,
                    'sender_id': sender_id,
                    'permission_level': permission_level,
                    'is_group': is_group,
                    'group_id': group_id,
                    'user_title': user_title,
                    'user_nickname': user_nickname,
                    'lock': asyncio.Lock(),
                    'last_proactive_at': 0.0,
                    'ephemeral_session': ephemeral_session,
                }

            # 获取用户 session
            user_data = self._user_sessions[session_key]
            user_session = user_data['session']
            reply_chunks = user_data['reply_chunks']
            her_name = user_data['her_name']
            user_data['last_activity_at'] = time.time()
            user_data.setdefault('lock', asyncio.Lock())
            user_data['session_key'] = session_key
            user_data['sender_id'] = sender_id
            user_data['permission_level'] = permission_level
            user_data['is_group'] = is_group
            user_data['group_id'] = group_id
            user_data['user_title'] = user_title
            user_data['user_nickname'] = user_nickname
            user_data['memory_enabled'] = should_persist_memory
            user_data['memory_context_used'] = should_use_memory_context
            user_data['ephemeral_session'] = ephemeral_session

            async with user_data['lock']:
                reply_chunks.clear()

                self.logger.info(f"发送消息到 AI (会话: {session_key}, length: {len(message)})")
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

                ai_reply = ''.join(reply_chunks).strip()

            if ai_reply:
                if user_data.get('memory_enabled'):
                    try:
                        count = await self._cache_session_delta(session_key, user_data)
                        if count:
                            self.logger.info(f"[管理员] 成功同步 {count} 条消息到 Memory Server (会话: {session_key})")
                    except Exception as e:
                        self.logger.error(f"记忆同步失败: {e}")
                else:
                    if user_data.get('memory_context_used'):
                        self.logger.info(f"[临时发送] 已使用记忆上下文但跳过记忆同步 (会话: {session_key})")
                    elif is_group:
                        self.logger.info(f"[群聊] 跳过记忆同步 (群: {group_id}, 用户: {sender_id})")
                    else:
                        self.logger.info(f"[非管理员] 跳过记忆同步 (用户: {sender_id}, 权限: {permission_level})")

                self.logger.info(f"AI 生成回复完成 (会话: {session_key}, length: {len(ai_reply)})")
                return ai_reply
            else:
                self.logger.warning("AI 未生成回复")
                return f"收到你的消息: {message}"

        except asyncio.TimeoutError:
            self.logger.warning(f"会话 {session_key} 处理超时，关闭并丢弃该会话")
            user_data = self._user_sessions.pop(session_key, None)
            session = user_data.get('session') if user_data else None
            if session:
                try:
                    await session.close()
                except Exception as close_error:
                    self.logger.warning(f"关闭超时会话失败: {close_error}")
            return None
        except Exception as e:
            self.logger.exception(f"AI 生成回复失败: {e}")
            return f"收到你的消息: {message}"
        finally:
            if ephemeral_session:
                user_data = self._user_sessions.pop(session_key, None)
                session = user_data.get('session') if user_data else None
                if session:
                    try:
                        await session.close()
                    except Exception as close_error:
                        self.logger.warning(f"关闭临时会话失败: {close_error}")


    async def _save_trusted_users_to_config(self):
        """持久化信任用户列表到 store"""
        try:
            users = self.permission_mgr.list_users()
            await self.store.set("trusted_users", users)
            self.logger.info(f"成功持久化 {len(users)} 个信任用户到 store")
            return True
        except Exception as e:
            self.logger.error(f"持久化配置失败: {e}")
            return False

    async def _save_trusted_groups_to_config(self):
        """持久化信任群聊列表到 store"""
        try:
            groups = self.group_permission_mgr.list_groups()
            await self.store.set("trusted_groups", groups)
            self.logger.info(f"成功持久化 {len(groups)} 个信任群聊到 store")
            return True
        except Exception as e:
            self.logger.error(f"持久化群聊配置失败: {e}")
            return False


    async def _invalidate_private_session(self, qq_number: str) -> None:
        session_key = self._build_session_key(sender_id=qq_number, is_group=False)

        async def _invalidate() -> None:
            user_data = self._user_sessions.get(session_key)
            if user_data and user_data.get("memory_enabled"):
                finalized = await self._finalize_user_memory_session(session_key, reason="permission_change")
                if finalized:
                    return

            user_data = self._user_sessions.pop(session_key, None)
            session = user_data.get("session") if user_data else None
            if session:
                await session.close()

        await self._run_with_session_lock(session_key, _invalidate)

    @staticmethod
    def _normalize_target_id(target_id: str) -> str:
        return str(target_id or "").strip()

    @classmethod
    def _validate_target_id(cls, target_id: str, *, field_name: str) -> str:
        normalized = cls._normalize_target_id(target_id)
        if not normalized:
            raise ValueError(f"{field_name} 不能为空")
        if not normalized.isdigit():
            raise ValueError(f"{field_name} 必须是纯数字")
        return normalized

    @staticmethod
    def _validate_outbound_message(message: str) -> str:
        normalized = str(message or "").strip()
        if not normalized:
            raise ValueError("message 不能为空")
        return normalized

    def _ensure_qq_client_connected(self):
        if not self.qq_client:
            raise RuntimeError("QQ 客户端未初始化")
        if not self.qq_client.ws:
            raise RuntimeError("OneBot 未连接，请先启动自动回复")

    def _resolve_private_message_target(self, target: str) -> tuple[str, Optional[str]]:
        normalized = self._normalize_target_id(target)
        if not normalized:
            raise ValueError("target 不能为空")
        if normalized.isdigit():
            return normalized, None
        if not self.permission_mgr:
            raise RuntimeError("权限管理器未初始化")

        matches = self.permission_mgr.find_users_by_nickname(normalized)
        if not matches:
            raise ValueError(f"NICKNAME_NOT_FOUND: 昵称 {normalized} 不在信任用户列表中")
        if len(matches) > 1:
            qq_list = ", ".join(user["qq"] for user in matches)
            raise ValueError(f"NICKNAME_AMBIGUOUS: 昵称 {normalized} 匹配到多个用户: {qq_list}")
        return matches[0]["qq"], normalized

    async def _send_private_message_impl(self, target_qq: str, outbound_prompt: str, *, resolved_nickname: Optional[str] = None, raw_target: Optional[str] = None):
        self._ensure_qq_client_connected()
        permission_level = self.permission_mgr.get_permission_level(target_qq) if self.permission_mgr else "none"
        is_admin_target = permission_level == "admin"
        effective_permission_level = "admin" if is_admin_target else "trusted"
        ai_reply = await self._generate_reply(
            outbound_prompt,
            effective_permission_level,
            target_qq,
            is_group=False,
            use_memory_context=is_admin_target,
            persist_memory=False,
            ephemeral_session=True,
        )
        if not ai_reply:
            return Err(SdkError("AI_EMPTY: AI 未生成可发送的私聊内容"))
        await self.qq_client.send_message(target_qq, ai_reply)
        self.logger.info(f"Sent AI private message to {target_qq} (length: {len(ai_reply)})")
        result = {"qq_number": target_qq, "prompt": outbound_prompt, "message": ai_reply}
        if raw_target:
            result["target"] = raw_target
        if resolved_nickname:
            result["nickname"] = resolved_nickname
        return Ok(result)

    @plugin_entry(
        id="send_private_message",
        name="按QQ号发送私聊",
        description="向指定 QQ 号发送一条私聊消息。必须提供 qq_number。",
        input_schema={
            "type": "object",
            "properties": {
                "qq_number": {
                    "type": "string",
                    "description": "目标 QQ 号",
                },
                "message": {
                    "type": "string",
                    "description": "要发送的消息内容",
                },
            },
            "required": ["qq_number", "message"],
        },
    )
    async def send_private_message(self, qq_number: str, message: str, **_):
        """通过插件面板生成 AI 私聊消息并发送到指定 QQ 号"""
        try:
            target_qq = self._validate_target_id(qq_number, field_name="qq_number")
            outbound_prompt = self._validate_outbound_message(message)
            return await self._send_private_message_impl(target_qq, outbound_prompt, raw_target=qq_number)
        except ValueError as e:
            return Err(SdkError(f"INVALID_ARGUMENT: {e}"))
        except RuntimeError as e:
            return Err(SdkError(f"SEND_NOT_READY: {e}"))
        except Exception as e:
            self.logger.error(f"Failed to send AI private message: {e}")
            return Err(SdkError(f"SEND_FAILED: 发送 AI 私聊消息失败: {e}"))

    @plugin_entry(
        id="send_private_message_by_nickname",
        name="按昵称发送私聊",
        description="向已配置昵称的用户发送一条私聊消息。必须提供 nickname。",
        input_schema={
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "trusted_users 中已配置的昵称",
                },
                "message": {
                    "type": "string",
                    "description": "要发送的消息内容",
                },
            },
            "required": ["nickname", "message"],
        },
    )
    async def send_private_message_by_nickname(self, nickname: str, message: str, **_):
        """通过插件面板生成 AI 私聊消息并按昵称发送到指定用户"""
        try:
            target_qq, resolved_nickname = self._resolve_private_message_target(nickname)
            outbound_prompt = self._validate_outbound_message(message)
            return await self._send_private_message_impl(
                target_qq,
                outbound_prompt,
                resolved_nickname=resolved_nickname,
                raw_target=nickname,
            )
        except ValueError as e:
            return Err(SdkError(f"INVALID_ARGUMENT: {e}"))
        except RuntimeError as e:
            return Err(SdkError(f"SEND_NOT_READY: {e}"))
        except Exception as e:
            self.logger.error(f"Failed to send AI private message by nickname: {e}")
            return Err(SdkError(f"SEND_FAILED: 按昵称发送 AI 私聊消息失败: {e}"))

    @plugin_entry(
        id="send_group_message",
        name="发送群聊消息",
        description="向指定 QQ 群发送一条群聊消息。",
        input_schema={
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "目标群号",
                },
                "message": {
                    "type": "string",
                    "description": "要发送的消息内容",
                },
            },
            "required": ["group_id", "message"],
        },
    )
    async def send_group_message(self, group_id: str, message: str, **_):
        """通过插件面板生成 AI 群聊消息并发送到指定群"""
        try:
            target_group = self._validate_target_id(group_id, field_name="group_id")
            outbound_prompt = self._validate_outbound_message(message)
            self._ensure_qq_client_connected()
            ai_reply = await self._generate_reply(
                outbound_prompt,
                "group",
                sender_id=target_group,
                is_group=True,
                group_id=target_group,
                use_memory_context=False,
                persist_memory=False,
                ephemeral_session=True,
                group_facing=True,
            )
            if not ai_reply:
                return Err(SdkError("AI_EMPTY: AI 未生成可发送的群聊内容"))
            await self.qq_client.send_group_message(target_group, ai_reply)
            self.logger.info(f"Sent AI group message to {target_group} (length: {len(ai_reply)})")
            return Ok({"group_id": target_group, "prompt": outbound_prompt, "message": ai_reply})
        except ValueError as e:
            return Err(SdkError(f"INVALID_ARGUMENT: {e}"))
        except RuntimeError as e:
            return Err(SdkError(f"SEND_NOT_READY: {e}"))
        except Exception as e:
            self.logger.error(f"Failed to send AI group message: {e}")
            return Err(SdkError(f"SEND_FAILED: 发送 AI 群聊消息失败: {e}"))

    @plugin_entry(
        id="add_trusted_user",
        name="添加信任用户",
        description="添加一个信任的 QQ 号到白名单。",
        input_schema={
            "type": "object",
            "properties": {
                "qq_number": {
                    "type": "string",
                    "description": "QQ 号",
                },
                "level": {
                    "type": "string",
                    "description": "权限等级: admin, trusted, normal",
                    "default": "trusted",
                },
                "nickname": {
                    "type": "string",
                    "description": "用户昵称（可选，管理员无需设置）",
                    "default": "",
                },
            },
            "required": ["qq_number"],
        },
    )
    async def add_trusted_user(self, qq_number: str, level: str = "trusted", nickname: str = "", **_):
        """添加信任用户并持久化到 store"""
        if not self.permission_mgr:
            return Err(SdkError("NOT_INITIALIZED: 权限管理器未初始化"))

        # 添加到内存（管理员不设置昵称）
        user_nickname = "" if level == "admin" else nickname
        self.permission_mgr.add_user(qq_number, level, user_nickname)
        self._refresh_admin_qq()
        await self._invalidate_private_session(qq_number)
        self.logger.info(f"Added trusted user: {qq_number} with level {level}" +
                        (f" and nickname {user_nickname}" if user_nickname else ""))

        # 持久化到 store
        success = await self._save_trusted_users_to_config()

        result_data = {
            "qq_number": qq_number,
            "level": level,
            "persisted": success,
        }
        if user_nickname:
            result_data["nickname"] = user_nickname
        if not success:
            result_data["warning"] = "已添加到内存，但持久化失败"
        return Ok(result_data)

    @plugin_entry(
        id="remove_trusted_user",
        name="移除信任用户",
        description="从白名单中移除一个 QQ 号。",
        input_schema={
            "type": "object",
            "properties": {
                "qq_number": {
                    "type": "string",
                    "description": "QQ 号",
                },
            },
            "required": ["qq_number"],
        },
    )
    async def remove_trusted_user(self, qq_number: str, **_):
        """移除信任用户并持久化到 store"""
        if not self.permission_mgr:
            return Err(SdkError("NOT_INITIALIZED: 权限管理器未初始化"))

        self.permission_mgr.remove_user(qq_number)
        self._refresh_admin_qq()
        await self._invalidate_private_session(qq_number)
        self.logger.info(f"Removed trusted user: {qq_number}")

        success = await self._save_trusted_users_to_config()
        result = {"qq_number": qq_number, "persisted": success}
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
                "qq_number": {
                    "type": "string",
                    "description": "QQ 号",
                },
                "nickname": {
                    "type": "string",
                    "description": "昵称（留空则清除昵称）",
                },
            },
            "required": ["qq_number"],
        },
    )
    async def set_user_nickname(self, qq_number: str, nickname: str = "", **_):
        """设置用户昵称并持久化到 store"""
        if not self.permission_mgr:
            return Err(SdkError("NOT_INITIALIZED: 权限管理器未初始化"))

        permission_level = self.permission_mgr.get_permission_level(qq_number)
        if permission_level == "none":
            return Err(SdkError(f"USER_NOT_FOUND: 用户 {qq_number} 不在信任列表中"))

        if permission_level == "admin":
            return Err(SdkError("ADMIN_NO_NICKNAME: 管理员始终被称为主人，无法设置昵称"))

        success = self.permission_mgr.set_nickname(qq_number, nickname)
        if not success:
            return Err(SdkError("SET_FAILED: 设置昵称失败"))

        persist_success = await self._save_trusted_users_to_config()
        action = "清除" if not nickname else "设置"
        self.logger.info(f"{action}用户 {qq_number} 的昵称: {nickname}")

        result = {
            "qq_number": qq_number,
            "nickname": nickname if nickname else None,
            "persisted": persist_success,
        }
        if not persist_success:
            result["warning"] = "已在内存中更新，但持久化失败"
        return Ok(result)

    @plugin_entry(
        id="add_trusted_group",
        name="添加信任群聊",
        description="添加一个信任的 QQ 群到白名单。",
        input_schema={
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号",
                },
                "level": {
                    "type": "string",
                    "description": "权限等级: trusted, open, normal",
                    "default": "normal",
                },
            },
            "required": ["group_id"],
        },
    )
    async def add_trusted_group(self, group_id: str, level: str = "normal", **_):
        """添加信任群聊并持久化到 store"""
        if not self.group_permission_mgr:
            return Err(SdkError("NOT_INITIALIZED: 群聊权限管理器未初始化"))

        self.group_permission_mgr.add_group(group_id, level)
        self.logger.info(f"Added trusted group: {group_id} with level {level}")

        success = await self._save_trusted_groups_to_config()
        result = {"group_id": group_id, "level": level, "persisted": success}
        if not success:
            result["warning"] = "已添加到内存，但持久化失败"
        return Ok(result)

    @plugin_entry(
        id="remove_trusted_group",
        name="移除信任群聊",
        description="从白名单中移除一个 QQ 群。",
        input_schema={
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号",
                },
            },
            "required": ["group_id"],
        },
    )
    async def remove_trusted_group(self, group_id: str, **_):
        """移除信任群聊并持久化到 store"""
        if not self.group_permission_mgr:
            return Err(SdkError("NOT_INITIALIZED: 群聊权限管理器未初始化"))

        self.group_permission_mgr.remove_group(group_id)
        self.logger.info(f"Removed trusted group: {group_id}")

        success = await self._save_trusted_groups_to_config()
        result = {"group_id": group_id, "persisted": success}
        if not success:
            result["warning"] = "已从内存移除，但持久化失败"
        return Ok(result)

    async def _start_napcat(self, show_window: bool = True) -> bool:
        """启动 NapCat

        Args:
            show_window: 是否显示窗口（True=前台启动，用于登录；False=后台启动）
        """
        try:
            # 获取 NapCat.Shell 目录
            plugin_dir = Path(__file__).parent
            napcat_dir = plugin_dir / "NapCat.Shell"
            launcher_script = napcat_dir / "launcher.bat"

            if not launcher_script.exists():
                self._manages_napcat_process = False
                self.logger.warning(f"NapCat launcher not found: {launcher_script}")
                return False

            mode = "前台" if show_window else "后台"
            self.logger.info(f"Starting NapCat ({mode}模式) from {napcat_dir}")

            if self._napcat_process and self._napcat_process.returncode is None:
                self.logger.info("NapCat process already running")
                onebot_url = str((self._cfg.get("qq_auto_reply", {}) or {}).get("onebot_url", "ws://127.0.0.1:3001"))
                await self._wait_onebot_ready(onebot_url)
                return True

            # 根据参数决定是否显示窗口
            if show_window:
                # 前台启动：强制通过 cmd 打开独立控制台窗口运行 launcher.bat
                creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                self._napcat_process = await asyncio.create_subprocess_exec(
                    "cmd", "/k", str(launcher_script),
                    cwd=str(napcat_dir),
                    creationflags=creationflags,
                )
            else:
                # 后台启动：隐藏窗口并丢弃控制台输出
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                self._napcat_process = await asyncio.create_subprocess_exec(
                    "cmd", "/c", str(launcher_script),
                    cwd=str(napcat_dir),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    startupinfo=startupinfo,
                )
            self._manages_napcat_process = True

            self.logger.info(f" NapCat started ({mode}模式, PID: {self._napcat_process.pid if self._napcat_process else 'N/A'})")

            onebot_url = str((self._cfg.get("qq_auto_reply", {}) or {}).get("onebot_url", "ws://127.0.0.1:3001"))
            await self._wait_onebot_ready(onebot_url)
            return True

        except Exception as e:
            self.logger.error(f"Failed to start NapCat: {e}")
            return False

    async def _wait_onebot_ready(self, onebot_url: str, timeout: float = 30.0) -> None:
        host, port = self._parse_onebot_host_port(onebot_url)
        deadline = time.monotonic() + timeout
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=1.0
                )
                writer.close()
                await writer.wait_closed()
                self.logger.info(f"OneBot endpoint is ready: {host}:{port}")
                return
            except (OSError, asyncio.TimeoutError) as e:
                last_error = e
                await asyncio.sleep(1)
        raise RuntimeError(f"OneBot endpoint not ready at {host}:{port}: {last_error}")

    def _parse_onebot_host_port(self, onebot_url: str) -> tuple[str, int]:
        raw = (onebot_url or "").strip()
        if raw.startswith(("ws://", "wss://", "http://", "https://")):
            raw = raw.split("://", 1)[1]
        raw = raw.split("/", 1)[0]
        if ":" in raw:
            host, port_text = raw.rsplit(":", 1)
            try:
                return host or "127.0.0.1", int(port_text)
            except ValueError:
                pass
        return "127.0.0.1", 3001


    @plugin_entry(
        id="start_qq_server",
        name="开启QQ服务器",
        description="开启 QQ 服务器。",
        input_schema={
            "type": "object",
            "properties": {
                "show_window": {
                    "type": "boolean",
                    "description": "是否显示窗口。true=前台启动，false=后台启动",
                    "default": True
                }
            }
        },
    )
    async def start_qq_server(self, show_window: bool = True, **_):
        """开启 QQ 服务器"""
        ready = await self._start_napcat(show_window=show_window)
        if not ready:
            return Err(SdkError("START_ERROR: 未找到内置 NapCat，请先手动启动外部 NapCat/OneBot 后再使用 start_auto_reply"))
        return Ok({"status": "started", "show_window": bool(show_window), "ready": True})

    @plugin_entry(
        id="stop_qq_server",
        name="关闭QQ服务器",
        description="关闭 QQ 服务器并断开连接。",
        input_schema={
            "type": "object",
        },
    )
    async def stop_qq_server(self, **_):
        """关闭 QQ 服务器"""
        await self._stop_auto_reply_runtime(stop_napcat=True)
        return Ok({"status": "stopped"})

    async def _stop_napcat(self):
        """停止 NapCat"""
        # 停止日志捕获任务
        if self._napcat_log_task:
            self._napcat_log_task.cancel()
            try:
                await self._napcat_log_task
            except asyncio.CancelledError:
                pass
            self._napcat_log_task = None
        if not self._manages_napcat_process:
            self.logger.info("当前为外部 OneBot/NapCat 模式，跳过 QQ/NapCat 进程关闭")
            self._napcat_process = None
            self._manages_napcat_process = False
            return

        # 用 KillQQ.bat 终止 QQ 进程
        try:
            self.logger.info("Stopping NapCat via KillQQ.bat...")
            plugin_dir = Path(__file__).parent
            kill_script = plugin_dir / "NapCat.Shell" / "KillQQ.bat"
            if kill_script.exists():
                kill_proc = await asyncio.create_subprocess_exec(
                    str(kill_script),
                    cwd=str(kill_script.parent),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(kill_proc.wait(), timeout=5.0)
                self.logger.info("KillQQ.bat executed")
            else:
                self.logger.warning("KillQQ.bat not found, falling back to taskkill QQ.exe")
                kill_proc = await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/IM", "QQ.exe",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(kill_proc.wait(), timeout=5.0)
        except Exception as e:
            self.logger.error(f"Failed to stop NapCat: {e}")

        self._napcat_process = None
        self._manages_napcat_process = False
