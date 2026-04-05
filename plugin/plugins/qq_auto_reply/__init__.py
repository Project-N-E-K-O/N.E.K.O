
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
import socket
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
        self._user_sessions: dict[str, dict[str, Any]] = {}

        # Normal 权限转述功能
        self._admin_qq: Optional[str] = None
        self._normal_relay_probability: float = 0.1

        # NapCat 进程管理
        self._napcat_process: Optional[asyncio.subprocess.Process] = None
        self._napcat_show_window = False
        self._napcat_log_task: Optional[asyncio.Task] = None

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

        # 初始化 QQ 客户端
        onebot_url = qq_cfg.get("onebot_url", "ws://127.0.0.1:3001")
        token = qq_cfg.get("token", "")
        self.qq_client = QQClient(onebot_url=onebot_url, token=token, logger=self.logger)
        self.logger.info(f"QQ 客户端已初始化: {onebot_url}")

        await self._start_napcat(show_window=True)

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
        description="用户说'启动'、'开始'、'开启自动回复'、'启动 QQ 监听'时调用此功能。开始监听 QQ 消息并自动回复。接收消息后根据权限等级生成 AI 回复。",
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
            await self.qq_client.connect()

            # 启动消息处理任务
            self._running = True
            self._message_task = asyncio.create_task(self._process_messages())

            self.logger.info("Auto reply started")
            return Ok({"status": "started"})
        except Exception as e:
            self.logger.exception("Failed to start auto reply")
            return Err(SdkError(f"START_ERROR: 启动失败: {e}"))

    @plugin_entry(
        id="stop_auto_reply",
        name="停止自动回复",
        description="stop。停止监听 QQ 消息，断开与 OneBot 服务的连接。用户说'停止'、'关闭'、'停止自动回复'时调用此功能。",
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

        if self.qq_client:
            await self.qq_client.disconnect()

        if stop_napcat:
            await self._stop_napcat()

    async def _process_messages(self):
        """处理接收到的 QQ 消息"""
        while self._running:
            try:
                message = await self.qq_client.receive_message()
                if message:
                    await self._handle_message(message)
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
        if not is_at_bot:
            self.logger.debug(f"Ignored group message without @: {group_id}")
            return

        # 生成回复（群聊中不检查用户权限）
        reply_text = await self._generate_reply(
            message_text, "group", sender_id,
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

    async def _flush_idle_memory_sessions(self):
        now = time.time()
        idle_users = []
        for sender_id, user_data in list(self._user_sessions.items()):
            if not user_data.get('memory_enabled'):
                continue
            last_activity_at = user_data.get('last_activity_at') or now
            if now - last_activity_at >= self.SESSION_IDLE_TIMEOUT_SECONDS:
                idle_users.append(sender_id)

        for sender_id in idle_users:
            await self._finalize_user_memory_session(sender_id, reason="idle_timeout")

    async def _flush_all_memory_sessions(self, reason: str):
        for sender_id, user_data in list(self._user_sessions.items()):
            if user_data.get('memory_enabled'):
                await self._finalize_user_memory_session(sender_id, reason=reason)

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

    async def _cache_session_delta(self, sender_id: str, user_data: dict[str, Any]) -> int:
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

    async def _finalize_user_memory_session(self, sender_id: str, reason: str) -> bool:
        user_data = self._user_sessions.get(sender_id)
        if not user_data or not user_data.get('memory_enabled'):
            return False

        session = user_data.get('session')
        her_name = user_data.get('her_name')
        if not session or not her_name:
            self._user_sessions.pop(sender_id, None)
            return False

        try:
            conversation_history = getattr(session, '_conversation_history', []) or []
            last_synced_index = int(user_data.get('last_synced_index', 0))
            remaining_messages = self._conversation_slice_to_memory_messages(conversation_history, last_synced_index)

            if remaining_messages:
                result = await self._post_memory_history('process', her_name, remaining_messages, timeout=30.0)
                if result.get('status') == 'error':
                    raise RuntimeError(result.get('message', 'process failed'))
                self.logger.info(f"[{reason}] 已为用户 {sender_id} 完成正式记忆结算，消息数: {len(remaining_messages)}")
            elif user_data.get('has_cached_memory'):
                settled_messages = self._conversation_slice_to_memory_messages(conversation_history, 0)
                result = await self._post_memory_history('settle', her_name, settled_messages, timeout=30.0)
                if result.get('status') == 'error':
                    raise RuntimeError(result.get('message', 'settle failed'))
                self.logger.info(f"[{reason}] 已为用户 {sender_id} 完成缓存记忆结算")

            await session.close()
            self._user_sessions.pop(sender_id, None)
            return True
        except Exception as e:
            self.logger.error(f"[{reason}] 用户 {sender_id} 的记忆结算失败: {e}")
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

            await temp_session.connect(instructions=system_prompt)

            # 发送转述请求
            relay_prompt = f"请把这个内容转述给{master_name if master_name else '主人'}：{message_text}"
            await temp_session.stream_text(relay_prompt)

            completed = await self._wait_session_response_complete(temp_session)
            if not completed:
                self.logger.warning("Relay generation timed out; closing temporary session")
                await temp_session.close()
                return

            relay_text = ''.join(reply_chunks).strip()

            if relay_text:
                # 发送给管理员
                try:
                    await self.qq_client.send_message(self._admin_qq, relay_text)
                    self.logger.info(f"Relayed to admin {self._admin_qq} (length: {len(relay_text)})")
                except Exception as e:
                    self.logger.error(f"Failed to relay to admin: {e}")

            # 断开临时会话
            await temp_session.close()

        except Exception as e:
            self.logger.error(f"Failed to generate relay message: {e}")


    async def _generate_reply(
        self, message: str, permission_level: str, sender_id: str,
        is_group: bool = False, group_id: str = None, user_nickname: Optional[str] = None
    ) -> Optional[str]:
        """生成回复内容（使用 OmniOfflineClient + Memory Server 同步）"""
        # 私聊：只为 admin 和 trusted 用户生成 AI 回复
        # 群聊：所有 @ 机器人的用户都生成回复
        if not is_group and permission_level not in ["admin", "trusted"]:
            return None

        try:
            from main_logic.omni_offline_client import OmniOfflineClient
            from utils.config_manager import get_config_manager
            import httpx
            from config import MEMORY_SERVER_PORT

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

            # 为每个 QQ 用户维护独立的对话客户端
            if not hasattr(self, '_user_sessions'):
                self._user_sessions = {}

            session_key = self._build_session_key(sender_id=sender_id, is_group=is_group, group_id=group_id)

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

                # 使用与前端完全一致的提示词结构
                from config.prompts_sys import SESSION_INIT_PROMPT
                from utils.language_utils import get_global_language

                # 获取用户语言
                user_language = get_global_language()

                memory_context = ""
                if not is_group and permission_level == "admin":
                    try:
                        async with httpx.AsyncClient(timeout=5.0) as client:
                            response = await client.get(f"http://localhost:{MEMORY_SERVER_PORT}/new_dialog/{her_name}")
                            if response.status_code == 200:
                                memory_context = response.text.strip()
                            else:
                                self.logger.warning(f"读取 Memory Server 上下文失败: {response.status_code}")
                    except Exception as e:
                        self.logger.warning(f"读取 Memory Server 上下文失败: {e}")

                # 构建初始提示（与前端一致）
                init_prompt = SESSION_INIT_PROMPT.get(user_language, SESSION_INIT_PROMPT['zh'])
                init_prompt = init_prompt.format(name=her_name)

                # 构建完整系统提示（与前端架构一致）
                system_prompt_parts = [
                    init_prompt,  # "你是一个角色扮演大师。请按要求扮演以下角色（{name}）。"
                    character_prompt  # 角色核心提示词
                ]

                if memory_context:
                    system_prompt_parts.append(memory_context)

                # 注入角色卡额外字段
                if character_card_fields:
                    system_prompt_parts.append("\n======角色卡额外设定======")
                    for field_name, field_value in character_card_fields.items():
                        system_prompt_parts.append(f"{field_name}: {field_value}")
                    system_prompt_parts.append("======角色卡设定结束======")

                # 添加 QQ 对话特定说明
                if is_group:
                    system_prompt_parts.append(f"""
======QQ 群聊环境======
- 你正在 QQ 群 {group_id} 中与用户 {sender_id} 对话
- 对方的称呼是：{user_title}
- 这是群聊环境，有多个用户在场
- 请保持角色设定，用简短自然的话回复（不超过50字）
- 不要使用 Markdown 格式，不要使用表情符号
- 记住你是 {her_name}，始终以 {her_name} 的身份回复
- 在回复中自然地称呼对方为"{user_title}"
- 注意不要重复之前的发言
======环境说明结束======""")
                else:
                    system_prompt_parts.append(f"""
======QQ 私聊环境======
- 你正在通过 QQ 与用户 {sender_id} 私聊
- 对方的称呼是：{user_title}
- 请保持角色设定，用简短自然的话回复（不超过50字）
- 不要使用 Markdown 格式，不要使用表情符号
- 记住你是 {her_name}，始终以 {her_name} 的身份回复
- 在回复中自然地称呼对方为"{user_title}“
- 注意不要重复之前的发言
======环境说明结束======""")

                system_prompt = "\n".join(system_prompt_parts)

                self.logger.info(f"系统提示词长度: {len(system_prompt)} 字符")
                self.logger.info(f"使用语言: {user_language}, 初始提示: {init_prompt[:50]}...")

                await user_session.connect(instructions=system_prompt)

                self._user_sessions[session_key] = {
                    'session': user_session,
                    'reply_chunks': reply_chunks,
                    'her_name': her_name,
                    'character_fields': character_card_fields,
                    'last_synced_index': 0,
                    'last_activity_at': time.time(),
                    'memory_enabled': (not is_group and permission_level == "admin"),
                    'has_cached_memory': False,
                }

            # 获取用户 session
            user_data = self._user_sessions[session_key]
            user_session = user_data['session']
            reply_chunks = user_data['reply_chunks']
            her_name = user_data['her_name']
            user_data['last_activity_at'] = time.time()

            reply_chunks.clear()

            self.logger.info(f"发送消息到 AI (会话: {session_key}, length: {len(message)})")
            await user_session.stream_text(message)

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
                    if is_group:
                        self.logger.info(f"[群聊] 跳过记忆同步 (群: {group_id}, 用户: {sender_id})")
                    else:
                        self.logger.info(f"[非管理员] 跳过记忆同步 (用户: {sender_id}, 权限: {permission_level})")

                self.logger.info(f"AI 生成回复完成 (会话: {session_key}, length: {len(ai_reply)})")
                return ai_reply
            else:
                self.logger.warning("AI 未生成回复")
                return f"收到你的消息: {message}"

        except Exception as e:
            self.logger.exception(f"AI 生成回复失败: {e}")
            return f"收到你的消息: {message}"


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
        user_data = self._user_sessions.pop(session_key, None)
        session = user_data.get("session") if user_data else None
        if session:
            await session.close()

    @plugin_entry(
        id="add_trusted_user",
        name="添加信任用户",
        description="【用户管理】添加一个信任的 QQ 号到白名单。支持三种权限等级：admin（管理员）、trusted（信任用户）、normal（普通用户）。可选设置昵称。用户说'添加用户'、'添加 QQ 号'、'添加信任用户'时调用。注意：这不是启动服务的功能。",
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
        description="【用户管理】从白名单中移除一个 QQ 号，移除后该用户将无法触发自动回复。用户说'移除用户'、'删除用户'时调用。",
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
        description="【用户管理】为信任用户设置专属称呼。管理员始终被称为主人，其他用户可以设置自定义昵称。用户说'设置昵称'、'修改昵称'时调用。",
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
        description="用户说'添加群聊'、'添加 QQ 群'时调用。添加一个信任的 QQ 群到白名单。支持两种等级：trusted（信任群聊）、normal（普通群聊）。",
        input_schema={
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号",
                },
                "level": {
                    "type": "string",
                    "description": "权限等级: trusted, normal",
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
        description="用户说'移除群聊'、'删除群聊'时调用。从白名单中移除一个 QQ 群，移除后该群将无法触发自动回复。",
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
                with socket.create_connection((host, port), timeout=1.0):
                    self.logger.info(f"OneBot endpoint is ready: {host}:{port}")
                    return
            except OSError as e:
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
        description="用户说'开启QQ服务器'、'启动QQ服务器'、'打开QQ服务器'时调用。开启 QQ 服务器（NapCat.Shell / OneBot），但不会自动启用 QQ 自动回复。",
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
            return Err(SdkError("START_ERROR: QQ 服务器未就绪"))
        return Ok({"status": "started", "show_window": bool(show_window), "ready": True})

    @plugin_entry(
        id="stop_qq_server",
        name="关闭QQ服务器",
        description="用户说'关闭QQ服务器'、'停止QQ服务器'、'关闭 NapCat'时调用。关闭 QQ 服务器并断开连接。",
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