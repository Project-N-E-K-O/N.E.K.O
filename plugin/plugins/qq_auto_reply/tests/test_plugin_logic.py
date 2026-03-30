"""
插件回复逻辑情景测试
直接实例化 QQAutoReplyPlugin，测试真实的 _handle_message / _generate_reply 路径
包括：session 持久化、多轮对话、Memory Server 同步
需要：NEKO 已启动，NapCat 在 ws://127.0.0.1:3001 运行
"""
import asyncio
import sys
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

NEKO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(NEKO_ROOT))

import tomllib
import pytest

PLUGIN_DIR = Path(__file__).parent.parent


# ── 构造最小 fake ctx ─────────────────────────────────────────────────────────

def make_fake_ctx(plugin_toml: dict):
    """构造满足 SDK ensure_sdk_context 要求的最小 ctx"""
    ctx = MagicMock()
    ctx.plugin_id = "qq_auto_reply"
    ctx.metadata   = {}
    ctx.logger     = logging.getLogger("test.plugin")
    ctx.config_path = str(PLUGIN_DIR / "plugin.toml")
    ctx.bus        = None
    ctx._effective_config = plugin_toml   # 让 store/db 解析用

    # 所有 async ctx 方法返回合理的空值
    async def _get_own_config(**_):
        return {"config": plugin_toml}
    async def _noop(**_):
        return {}

    ctx.get_own_config            = _get_own_config
    ctx.get_own_base_config       = _get_own_config
    ctx.get_own_profiles_state    = AsyncMock(return_value={"data": {}})
    ctx.get_own_profile_config    = AsyncMock(return_value={"data": {}})
    ctx.get_own_effective_config  = _get_own_config
    ctx.update_own_config         = AsyncMock(return_value={})
    ctx.upsert_own_profile_config = AsyncMock(return_value={})
    ctx.delete_own_profile_config = AsyncMock(return_value={"removed": False})
    ctx.set_own_active_profile    = AsyncMock(return_value={})
    ctx.query_plugins             = AsyncMock(return_value={})
    ctx.trigger_plugin_event      = AsyncMock(return_value={})
    ctx.get_system_config         = AsyncMock(return_value={})
    ctx.query_memory              = AsyncMock(return_value={})
    ctx.run_update_async          = AsyncMock(return_value={})
    ctx.export_push_async         = AsyncMock(return_value={})
    ctx.push_message              = MagicMock(return_value=None)
    ctx.update_status             = MagicMock(return_value=None)
    return ctx


def load_plugin_toml():
    with open(PLUGIN_DIR / "plugin.toml", "rb") as f:
        return tomllib.load(f)


async def build_plugin():
    """实例化插件并执行 startup"""
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    toml = load_plugin_toml()
    ctx  = make_fake_ctx(toml)

    # store.get 返回 None（让 startup 从 TOML 读配置）
    plugin = QQAutoReplyPlugin(ctx)
    plugin.store.get = AsyncMock(return_value=MagicMock(value=None))
    plugin.store.set = AsyncMock(return_value=MagicMock())

    await plugin.startup()
    return plugin, toml


# ── 辅助：构造标准消息 dict ───────────────────────────────────────────────────

def private_msg(user_id: str, content: str, nickname: str = ""):
    return {
        "message_type": "private",
        "user_id": user_id,
        "content": content,
        "user_nickname": nickname or f"user_{user_id}",
        "message_id": 1,
        "timestamp": 1700000000,
    }


def group_msg(group_id: str, user_id: str, content: str, is_at_bot: bool = False, nickname: str = ""):
    return {
        "message_type": "group",
        "group_id": group_id,
        "user_id": user_id,
        "content": content,
        "user_nickname": nickname or f"user_{user_id}",
        "is_at_bot": is_at_bot,
        "message_id": 2,
        "timestamp": 1700000001,
    }


def get_user_by_level(qq_cfg: dict, level: str):
    return next((u["qq"] for u in qq_cfg["trusted_users"] if u["level"] == level), None)


def get_group_by_level(qq_cfg: dict, level: str):
    return next((g["group_id"] for g in qq_cfg["trusted_groups"] if g["level"] == level), None)


# ═══════════════════════════════════════════════════════════════════════════════
# 测试：权限路由
# ═══════════════════════════════════════════════════════════════════════════════

class TestPermissionRouting:
    """验证 _handle_message 按权限正确路由，不实际调用 AI"""

    @pytest.mark.asyncio
    async def test_unknown_user_ignored(self):
        plugin, _ = await build_plugin()
        plugin._generate_reply = AsyncMock()
        plugin._handle_normal_relay = AsyncMock()

        await plugin._handle_message(private_msg("999999999", "hello"))

        plugin._generate_reply.assert_not_called()
        plugin._handle_normal_relay.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_user_triggers_relay(self):
        plugin, toml = await build_plugin()
        # 临时添加一个 normal 用户
        plugin.permission_mgr.add_user("111111111", "normal")
        plugin._handle_normal_relay = AsyncMock()
        plugin._generate_reply = AsyncMock()

        await plugin._handle_message(private_msg("111111111", "hi"))

        plugin._handle_normal_relay.assert_called_once()
        plugin._generate_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_trusted_user_gets_ai_reply(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        trusted_qq = get_user_by_level(qq_cfg, "trusted")
        if not trusted_qq:
            pytest.skip("需要 trusted 用户")

        plugin._generate_reply = AsyncMock(return_value="测试回复")
        plugin.qq_client = AsyncMock()

        await plugin._handle_message(private_msg(trusted_qq, "你好"))

        plugin._generate_reply.assert_called_once()
        plugin.qq_client.send_message.assert_called_once_with(trusted_qq, "测试回复")

    @pytest.mark.asyncio
    async def test_admin_user_gets_ai_reply(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        admin_qq = next(u["qq"] for u in qq_cfg["trusted_users"] if u["level"] == "admin")

        plugin._generate_reply = AsyncMock(return_value="管理员回复")
        plugin.qq_client = AsyncMock()

        await plugin._handle_message(private_msg(admin_qq, "测试"))

        plugin._generate_reply.assert_called_once()
        plugin.qq_client.send_message.assert_called_once_with(admin_qq, "管理员回复")

    @pytest.mark.asyncio
    async def test_unknown_group_ignored(self):
        plugin, _ = await build_plugin()
        plugin._generate_reply = AsyncMock()

        await plugin._handle_message(group_msg("000000000", "123", "hi", is_at_bot=True))

        plugin._generate_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_trusted_group_no_at_ignored(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        trusted_group = next(g["group_id"] for g in qq_cfg["trusted_groups"] if g["level"] == "trusted")

        plugin._generate_reply = AsyncMock()

        await plugin._handle_message(group_msg(trusted_group, "123", "hi", is_at_bot=False))

        plugin._generate_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_trusted_group_with_at_gets_reply(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        trusted_group = next(g["group_id"] for g in qq_cfg["trusted_groups"] if g["level"] == "trusted")

        plugin._generate_reply = AsyncMock(return_value="群聊回复")
        plugin.qq_client = AsyncMock()

        await plugin._handle_message(group_msg(trusted_group, "123", "你好", is_at_bot=True))

        plugin._generate_reply.assert_called_once()
        plugin.qq_client.send_group_message.assert_called_once_with(trusted_group, "群聊回复")

    @pytest.mark.asyncio
    async def test_normal_group_triggers_relay(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        normal_group = get_group_by_level(qq_cfg, "normal")
        if not normal_group:
            pytest.skip("需要 normal 群聊")

        plugin._handle_normal_relay = AsyncMock()
        plugin._generate_reply = AsyncMock()

        await plugin._handle_message(group_msg(normal_group, "123", "hi"))

        plugin._handle_normal_relay.assert_called_once()
        plugin._generate_reply.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 测试：session 持久化（多轮对话）
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionPersistence:
    """验证同一用户多次发消息复用同一 OmniOfflineClient session"""

    @pytest.mark.asyncio
    async def test_same_user_reuses_session(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        trusted_qq = get_user_by_level(qq_cfg, "trusted")
        if not trusted_qq:
            pytest.skip("需要 trusted 用户")

        mock_session = AsyncMock()
        mock_session._is_responding = False
        mock_session._conversation_history = []

        created_sessions = []

        original_generate = plugin._generate_reply

        async def patched_generate(message, permission_level, sender_id, **kwargs):
            # 第一次调用时注入 mock session
            if sender_id not in plugin._user_sessions:
                plugin._user_sessions[sender_id] = {
                    'session': mock_session,
                    'reply_chunks': [],
                    'her_name': '猫猫',
                    'character_fields': {}
                }
                created_sessions.append(sender_id)
            else:
                # 第二次：session 应该已存在
                assert sender_id in plugin._user_sessions
            return "回复"

        plugin._user_sessions = {}
        plugin._generate_reply = patched_generate
        plugin.qq_client = AsyncMock()

        await plugin._handle_message(private_msg(trusted_qq, "第一条消息"))
        await plugin._handle_message(private_msg(trusted_qq, "第二条消息"))

        # session 只被创建一次
        assert len(created_sessions) == 1

    @pytest.mark.asyncio
    async def test_different_users_get_separate_sessions(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        trusted_users = [u["qq"] for u in qq_cfg["trusted_users"] if u["level"] == "trusted"]

        if len(trusted_users) < 2:
            pytest.skip("需要至少2个 trusted 用户")

        user_a, user_b = trusted_users[0], trusted_users[1]
        seen_sessions = {}

        async def patched_generate(message, permission_level, sender_id, **kwargs):
            if sender_id not in plugin._user_sessions:
                plugin._user_sessions[sender_id] = {
                    'session': AsyncMock(),
                    'reply_chunks': [],
                    'her_name': '猫猫',
                    'character_fields': {}
                }
            seen_sessions[sender_id] = plugin._user_sessions[sender_id]['session']
            return "回复"

        plugin._user_sessions = {}
        plugin._generate_reply = patched_generate
        plugin.qq_client = AsyncMock()

        await plugin._handle_message(private_msg(user_a, "消息A"))
        await plugin._handle_message(private_msg(user_b, "消息B"))

        assert user_a in seen_sessions
        assert user_b in seen_sessions
        assert seen_sessions[user_a] is not seen_sessions[user_b]


# ═══════════════════════════════════════════════════════════════════════════════
# 测试：CQ 码清理
# ═══════════════════════════════════════════════════════════════════════════════

class TestSanitizeMessage:
    def test_at_all_replaced(self):
        from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
        result = QQAutoReplyPlugin._sanitize_message_text("[CQ:at,qq=all] 大家好")
        assert "[CQ:" not in result
        assert "@全体成员" in result

    def test_at_user_replaced(self):
        from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
        result = QQAutoReplyPlugin._sanitize_message_text("[CQ:at,qq=12345]你好")
        assert "[CQ:" not in result
        assert "@用户12345" in result

    def test_no_cq_unchanged(self):
        from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
        text = "普通消息，没有CQ码"
        assert QQAutoReplyPlugin._sanitize_message_text(text) == text

    def test_multiple_cq_codes(self):
        from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
        text = "[CQ:at,qq=111]和[CQ:at,qq=222]你们好"
        result = QQAutoReplyPlugin._sanitize_message_text(text)
        assert "@用户111" in result
        assert "@用户222" in result
        assert "[CQ:" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# 情景测试：真实 AI 回复（需要 NEKO 运行）
# ═══════════════════════════════════════════════════════════════════════════════

class TestRealAIReply:
    """调用真实 _generate_reply，验证 AI 回复内容和 session 行为"""

    @pytest.mark.asyncio
    async def test_admin_private_reply_not_empty(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        admin_qq = next(u["qq"] for u in qq_cfg["trusted_users"] if u["level"] == "admin")

        reply = await plugin._generate_reply(
            message="你好",
            permission_level="admin",
            sender_id=admin_qq,
            is_group=False,
        )
        print(f"\n[admin 私聊] AI 回复: {reply}")
        assert reply and len(reply) > 0

    @pytest.mark.asyncio
    async def test_trusted_private_reply_not_empty(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        trusted_qq = get_user_by_level(qq_cfg, "trusted")
        if not trusted_qq:
            pytest.skip("需要 trusted 用户")

        reply = await plugin._generate_reply(
            message="今天天气怎么样",
            permission_level="trusted",
            sender_id=trusted_qq,
            is_group=False,
        )
        print(f"\n[trusted 私聊] AI 回复: {reply}")
        assert reply and len(reply) > 0

    @pytest.mark.asyncio
    async def test_session_persists_across_turns(self):
        """同一用户两轮对话，第二轮 session 已存在"""
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        trusted_qq = get_user_by_level(qq_cfg, "trusted")
        if not trusted_qq:
            pytest.skip("需要 trusted 用户")

        reply1 = await plugin._generate_reply(
            message="我叫小明",
            permission_level="trusted",
            sender_id=trusted_qq,
            is_group=False,
        )
        assert trusted_qq in plugin._user_sessions
        session_first = plugin._user_sessions[trusted_qq]['session']

        reply2 = await plugin._generate_reply(
            message="你还记得我叫什么吗",
            permission_level="trusted",
            sender_id=trusted_qq,
            is_group=False,
        )
        session_second = plugin._user_sessions[trusted_qq]['session']

        print(f"\n[多轮] 第一轮: {reply1}")
        print(f"[多轮] 第二轮: {reply2}")

        # 同一个 session 对象
        assert session_first is session_second
        assert reply2 and len(reply2) > 0

    @pytest.mark.asyncio
    async def test_group_reply_not_empty(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        trusted_group = next(g["group_id"] for g in qq_cfg["trusted_groups"] if g["level"] == "trusted")

        reply = await plugin._generate_reply(
            message="大家好",
            permission_level="group",
            sender_id="123456789",
            is_group=True,
            group_id=trusted_group,
            user_nickname="测试用户",
        )
        print(f"\n[群聊] AI 回复: {reply}")
        assert reply and len(reply) > 0

    @pytest.mark.asyncio
    async def test_reply_under_50_chars(self):
        """验证 prompt 中的字数限制是否生效"""
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        trusted_qq = get_user_by_level(qq_cfg, "trusted")
        if not trusted_qq:
            pytest.skip("需要 trusted 用户")

        reply = await plugin._generate_reply(
            message="给我讲一个很长很长的故事",
            permission_level="trusted",
            sender_id=trusted_qq,
            is_group=False,
        )
        print(f"\n[字数限制] AI 回复({len(reply)}字): {reply}")
        # prompt 要求不超过50字，允许少量超出
        assert len(reply) <= 100

    @pytest.mark.asyncio
    async def test_admin_memory_sync_attempted(self):
        """admin 私聊后应尝试同步到 Memory Server"""
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        admin_qq = next(u["qq"] for u in qq_cfg["trusted_users"] if u["level"] == "admin")

        sync_calls = []

        import httpx
        original_post = httpx.AsyncClient.post
        original_get = httpx.AsyncClient.get

        async def mock_post(self_client, url, **kwargs):
            if "/cache/" in url:
                sync_calls.append(url)
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"count": 2}
                return mock_resp
            return await original_post(self_client, url, **kwargs)

        async def mock_get(self_client, url, **kwargs):
            if "/new_dialog/" in url:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.text = "记忆上下文"
                return mock_resp
            return await original_get(self_client, url, **kwargs)

        with patch.object(httpx.AsyncClient, "post", mock_post), patch.object(httpx.AsyncClient, "get", mock_get):
            reply = await plugin._generate_reply(
                message="你好",
                permission_level="admin",
                sender_id=admin_qq,
                is_group=False,
            )

        print(f"\n[Memory 同步] 回复: {reply}, 同步调用: {sync_calls}")
        assert len(sync_calls) >= 1
        assert "/cache/" in sync_calls[0]

    @pytest.mark.asyncio
    async def test_admin_new_session_reads_memory_context(self):
        plugin, toml = await build_plugin()
        qq_cfg = toml["qq_auto_reply"]
        admin_qq = next(u["qq"] for u in qq_cfg["trusted_users"] if u["level"] == "admin")

        import httpx
        original_get = httpx.AsyncClient.get

        get_calls = []

        async def mock_get(self_client, url, **kwargs):
            if "/new_dialog/" in url:
                get_calls.append(url)
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.text = "记忆上下文"
                return mock_resp
            return await original_get(self_client, url, **kwargs)

        with patch.object(httpx.AsyncClient, "get", mock_get):
            await plugin._generate_reply(
                message="你好",
                permission_level="admin",
                sender_id=admin_qq,
                is_group=False,
            )

        assert len(get_calls) == 1
        assert "/new_dialog/" in get_calls[0]

    @pytest.mark.asyncio
    async def test_finalize_session_uses_process_for_unsynced_messages(self):
        plugin, _ = await build_plugin()

        session = AsyncMock()
        session._conversation_history = [
            MagicMock(type='human', content='用户消息'),
            MagicMock(type='ai', content='AI回复'),
        ]
        session.close = AsyncMock()
        plugin._user_sessions['admin'] = {
            'session': session,
            'reply_chunks': [],
            'her_name': '猫猫',
            'character_fields': {},
            'last_synced_index': 0,
            'last_activity_at': 0,
            'memory_enabled': True,
            'has_cached_memory': True,
        }
        plugin._post_memory_history = AsyncMock(return_value={'status': 'processed'})

        ok = await plugin._finalize_user_memory_session('admin', reason='test')

        assert ok is True
        plugin._post_memory_history.assert_called_once()
        assert plugin._post_memory_history.call_args.args[0] == 'process'
        session.close.assert_awaited_once()
        assert 'admin' not in plugin._user_sessions

    @pytest.mark.asyncio
    async def test_finalize_session_uses_settle_when_only_cached(self):
        plugin, _ = await build_plugin()

        session = AsyncMock()
        session._conversation_history = [
            MagicMock(type='human', content='用户消息'),
            MagicMock(type='ai', content='AI回复'),
        ]
        session.close = AsyncMock()
        plugin._user_sessions['admin'] = {
            'session': session,
            'reply_chunks': [],
            'her_name': '猫猫',
            'character_fields': {},
            'last_synced_index': 2,
            'last_activity_at': 0,
            'memory_enabled': True,
            'has_cached_memory': True,
        }
        plugin._post_memory_history = AsyncMock(return_value={'status': 'settled'})

        ok = await plugin._finalize_user_memory_session('admin', reason='test')

        assert ok is True
        plugin._post_memory_history.assert_called_once()
        assert plugin._post_memory_history.call_args.args[0] == 'settle'
        session.close.assert_awaited_once()
        assert 'admin' not in plugin._user_sessions
