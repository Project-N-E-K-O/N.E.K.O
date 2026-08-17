from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import tomllib
from unittest.mock import AsyncMock

import pytest

from plugin.plugins.bilibili_dm import BiliDMPlugin
from plugin.plugins.bilibili_dm.bili_client import BiliDMClient
from plugin.plugins.bilibili_dm.config_store import BiliDMConfigStore
from plugin.plugins.bilibili_dm.permission import PermissionManager
from plugin.sdk.plugin import Err, Ok


def make_plugin(tmp_path: Path) -> BiliDMPlugin:
    plugin = object.__new__(BiliDMPlugin)
    plugin.ctx = SimpleNamespace(plugin_id="bilibili_dm")
    plugin.config_store = BiliDMConfigStore(tmp_path)
    plugin._settings = plugin.config_store.default_config()
    plugin._running = False
    plugin._message_task = None
    plugin._session_housekeeping_task = None
    plugin._handler_tasks = set()
    plugin._lifecycle_lock = asyncio.Lock()
    plugin._user_sessions = {}
    plugin._session_locks = {}
    plugin._session_locks_guard = asyncio.Lock()
    plugin._max_concurrent_messages = 3
    plugin._message_concurrency = asyncio.Semaphore(3)
    plugin._ai_connect_timeout_seconds = 10.0
    plugin._ai_turn_timeout_seconds = 60.0
    plugin._handler_shutdown_timeout_seconds = 10.0
    plugin._permission_mode = "allow_list"
    plugin.permission_mgr = PermissionManager([])
    plugin.bili_client = None
    plugin.logger = SimpleNamespace(
        debug=lambda *_: None,
        error=lambda *_: None,
        exception=lambda *_: None,
        info=lambda *_: None,
        warning=lambda *_: None,
    )
    return plugin


@pytest.mark.asyncio
async def test_config_store_persists_credentials_in_runtime_data_file(tmp_path):
    store = BiliDMConfigStore(tmp_path)

    saved = await store.save(
        {
            "sesdata": "sess-secret",
            "bili_jct": "csrf-secret",
            "dedeuserid": "123456",
            "permission_mode": "open",
            "max_concurrent_messages": 999,
            "unknown": "drop-me",
        }
    )

    assert store.path == tmp_path / "business_config.json"
    assert saved["sesdata"] == "sess-secret"
    assert saved["permission_mode"] == "open"
    assert saved["max_concurrent_messages"] == 20
    assert saved["enable_comment_notifications"] is True
    assert saved["notification_poll_interval_seconds"] == 20
    assert "unknown" not in saved

    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["sesdata"] == "sess-secret"
    assert raw["bili_jct"] == "csrf-secret"
    assert await store.load() == saved


@pytest.mark.asyncio
async def test_config_store_normalizes_comment_notification_settings(tmp_path):
    store = BiliDMConfigStore(tmp_path)

    saved = await store.save(
        {
            "enable_comment_notifications": False,
            "notification_poll_interval_seconds": 1,
            "notification_max_items": 999,
        }
    )

    assert saved["enable_comment_notifications"] is False
    assert saved["notification_poll_interval_seconds"] == 5
    assert saved["notification_max_items"] == 50


def test_comment_notification_target_and_deduplication():
    notification = {
        "id": 101,
        "item": {
            "business_id": "1",
            "subject_id": "987654",
            "root_id": "11",
            "source_id": "12",
            "target_id": "13",
            "source_content": "新评论",
            "target_reply_content": "被回复内容",
            "root_reply_content": "根评论内容",
        },
    }
    target = BiliDMClient._comment_reply_target(notification)

    assert target == {"type": 1, "oid": 987654, "root": 11, "parent": 12}
    assert BiliDMClient._notification_content(notification) == (
        "新评论\n[被回复评论] 被回复内容\n[根评论] 根评论内容"
    )
    assert BiliDMClient._video_aid_from_notification(notification) == 987654
    notification["item"]["business_id"] = "17"
    assert BiliDMClient._video_aid_from_notification(notification) is None
    notification["item"]["business_id"] = "1"

    client = object.__new__(BiliDMClient)
    client._notification_seen = []
    client._notification_seen_set = set()
    assert client._mark_notification_seen("reply", notification) is True
    assert client._mark_notification_seen("reply", notification) is False
    assert client._mark_notification_seen("at", notification) is True


def test_comment_and_private_messages_use_separate_sessions():
    assert BiliDMPlugin._build_session_key("42") == "bili:dm:42"
    assert (
        BiliDMPlugin._build_session_key("42", "comment:1:987654:11")
        == "bili:comment:1:987654:11:42"
    )
    assert BiliDMPlugin._build_session_key(
        "42", "comment:1:987654:11"
    ) != BiliDMPlugin._build_session_key("42", "comment:1:987654:22")


@pytest.mark.asyncio
async def test_comment_notification_uses_root_comment_as_conversation():
    client = object.__new__(BiliDMClient)
    client._credential = SimpleNamespace(dedeuserid="999")
    client._current_uid = "999"
    client._message_queue = asyncio.Queue()
    notification = {
        "id": 101,
        "user": {"mid": 42, "nickname": "tester"},
        "item": {
            "business_id": 1,
            "subject_id": 987654,
            "root_id": 11,
            "source_id": 12,
            "source_content": "新评论",
        },
    }

    await client._enqueue_comment_notification(notification, "reply")
    message = client._message_queue.get_nowait()

    assert message["conversation_key"] == "comment:1:987654:11"


@pytest.mark.asyncio
async def test_notification_bootstrap_waits_for_each_feed_and_preserves_tail():
    client = object.__new__(BiliDMClient)
    client.logger = SimpleNamespace(warning=lambda *_: None)
    client._notification_bootstrap_done = {"reply": False, "at": False}
    client._notification_seen = []
    client._notification_seen_set = set()
    client._notification_pending = deque()
    client._notification_max_items = 1
    client._current_uid = "999"
    client._enqueue_comment_notification = AsyncMock()
    client._is_at_current_user = lambda _: True

    reply_old = {"id": "reply-old"}
    reply_new = {"id": "reply-new"}
    at_old = {"id": "at-old"}
    at_new = {"id": "at-new"}
    at_newer = {"id": "at-newer"}
    calls = {"reply": 0, "at": 0}

    async def get_items(_http_client, url):
        source = "reply" if url.endswith("/reply") else "at"
        call = calls[source]
        calls[source] += 1
        if source == "reply":
            if call == 0:
                raise RuntimeError("temporary reply failure")
            return [reply_new, reply_old] if call >= 2 else [reply_old]
        if call >= 2:
            return [at_newer, at_new, at_old]
        return [at_new, at_old] if call == 1 else [at_old]

    client._get_notification_items = get_items

    await client._poll_comment_notifications()
    assert client._notification_bootstrap_done == {"reply": False, "at": True}
    client._enqueue_comment_notification.assert_not_awaited()

    await client._poll_comment_notifications()
    assert client._notification_bootstrap_done == {"reply": True, "at": True}
    client._enqueue_comment_notification.assert_awaited_once_with(at_new, "at")

    await client._poll_comment_notifications()
    assert client._enqueue_comment_notification.await_count == 2
    client._enqueue_comment_notification.assert_awaited_with(reply_new, "reply")
    assert list(client._notification_pending) == [("at", at_newer)]

    await client._poll_comment_notifications()
    assert client._enqueue_comment_notification.await_count == 3
    client._enqueue_comment_notification.assert_awaited_with(at_newer, "at")
    assert not client._notification_pending


@pytest.mark.asyncio
async def test_comment_reply_uses_signed_bili_request_and_logs_response(monkeypatch):
    from bilibili_api.utils import network as bili_network
    from bilibili_api.utils import utils as bili_utils

    captured: dict[str, object] = {}

    class FakeResponse:
        code = 200

        @staticmethod
        def utf8_text():
            return '{"code":0,"message":"0","data":{"rpid":778899}}'

    class FakeApi:
        def __init__(self, **kwargs):
            captured["api_kwargs"] = kwargs

        def update_data(self, **data):
            captured["data"] = data
            return self

        async def request(self, **kwargs):
            captured["request_kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(bili_network, "Api", FakeApi)
    monkeypatch.setattr(
        bili_utils,
        "get_api",
        lambda _: {
            "comment": {
                "send": {
                    "url": "https://api.bilibili.com/x/v2/reply/add",
                    "method": "POST",
                    "verify": True,
                    "wbi": True,
                    "dm": True,
                }
            }
        },
    )

    messages: list[str] = []
    client = object.__new__(BiliDMClient)
    client._credential = object()
    client.logger = SimpleNamespace(
        info=messages.append,
        error=messages.append,
    )

    result = await client.send_comment_reply(
        {"type": 1, "oid": 987654, "root": 123}, "测试回复"
    )

    assert result["data"]["rpid"] == 778899
    assert captured["request_kwargs"] == {"bili_res": True}
    assert captured["data"] == {
        "type": 1,
        "oid": 987654,
        "message": "测试回复",
        "plat": 1,
        "statistics": {"appId": 100, "platform": 5},
        "gaia_source": "main_web",
        "root": 123,
        "parent": 123,
    }
    assert captured["api_kwargs"]["wbi"] is True
    assert captured["api_kwargs"]["dm"] is True
    assert any(
        "HTTP 200" in message and '"rpid":778899' in message for message in messages
    )


@pytest.mark.asyncio
async def test_comment_reply_rejects_success_response_without_rpid(monkeypatch):
    from bilibili_api.utils import network as bili_network
    from bilibili_api.utils import utils as bili_utils

    class FakeResponse:
        code = 200

        @staticmethod
        def utf8_text():
            return '{"code":0,"message":"OK","data":{}}'

    class FakeApi:
        def __init__(self, **_kwargs):
            pass

        def update_data(self, **_data):
            return self

        async def request(self, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(bili_network, "Api", FakeApi)
    monkeypatch.setattr(
        bili_utils,
        "get_api",
        lambda _: {"comment": {"send": {"url": "test", "method": "POST"}}},
    )
    client = object.__new__(BiliDMClient)
    client._credential = object()
    client.logger = None

    with pytest.raises(RuntimeError, match="缺少 rpid"):
        await client.send_comment_reply({"type": 1, "oid": 987654}, "测试回复")


@pytest.mark.asyncio
async def test_disconnect_cancels_all_background_tasks():
    client = object.__new__(BiliDMClient)
    client._running = True
    client.logger = None
    client._session = SimpleNamespace(close=lambda: None)
    client._session_task = asyncio.create_task(asyncio.sleep(60))
    client._notification_task = asyncio.create_task(asyncio.sleep(60))
    tasks = (client._session_task, client._notification_task)

    await client.disconnect()

    assert all(task.done() for task in tasks)
    assert client._session_task is None
    assert client._notification_task is None


@pytest.mark.asyncio
async def test_dm_failure_does_not_stop_comment_notifications():
    client = object.__new__(BiliDMClient)
    client._running = True
    client.logger = SimpleNamespace(error=lambda *_: None)
    client._session = SimpleNamespace(
        start=AsyncMock(side_effect=RuntimeError("dm failed"))
    )

    await client._run_session()

    assert client._running is True


def test_at_feed_is_trusted_when_current_uid_cannot_be_resolved():
    client = object.__new__(BiliDMClient)
    client._current_uid = ""

    assert client._is_at_current_user({"item": {"at_details": []}}) is True


def test_at_feed_without_at_details_is_not_discarded():
    client = object.__new__(BiliDMClient)
    client._current_uid = "999"

    assert client._is_at_current_user({"item": {}}) is True


@pytest.mark.asyncio
async def test_permission_change_invalidates_all_user_sessions(tmp_path):
    plugin = make_plugin(tmp_path)
    dm_session = SimpleNamespace(close=AsyncMock())
    comment_session = SimpleNamespace(close=AsyncMock())
    other_session = SimpleNamespace(close=AsyncMock())
    plugin._user_sessions = {
        "bili:dm:42": {"sender_uid": "42", "session": dm_session},
        "bili:comment:1:9:10:42": {
            "sender_uid": "42",
            "session": comment_session,
        },
        "bili:dm:7": {"sender_uid": "7", "session": other_session},
    }

    await plugin._invalidate_user_sessions("42")

    assert set(plugin._user_sessions) == {"bili:dm:7"}
    dm_session.close.assert_awaited_once()
    comment_session.close.assert_awaited_once()
    other_session.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_and_comment_prompts_are_separate(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    monkeypatch.setattr(
        "utils.language_utils.get_global_language_full", lambda: "zh-CN"
    )
    args = {
        "her_name": "Neko",
        "master_name": "Master",
        "character_prompt": "character prompt",
        "character_card_fields": {},
        "permission_level": "trusted",
        "sender_uid": "42",
        "user_title": "Tester",
    }

    dm_prompt = await plugin._build_session_instructions(**args, channel_kind="dm")
    comment_prompt = await plugin._build_session_instructions(
        **args, channel_kind="comment"
    )

    assert "B站私聊环境" in dm_prompt
    assert "B站公开评论环境" not in dm_prompt
    assert "B站公开评论环境" in comment_prompt
    assert "不是私信对话" in comment_prompt
    assert "B站私聊环境" not in comment_prompt


@pytest.mark.asyncio
async def test_public_comment_generation_failure_has_no_context_fallback(
    tmp_path, monkeypatch
):
    plugin = make_plugin(tmp_path)

    def fail_config():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("utils.config_manager.get_config_manager", fail_config)
    internal_message = "[来自 B站用户 Tester（UID: 42）的评论] 内部上下文"

    comment_reply = await plugin._generate_reply(
        message=internal_message,
        permission_level="trusted",
        sender_uid="42",
        conversation_key="comment:1:2:3",
        channel_kind="comment",
    )
    dm_reply = await plugin._generate_reply(
        message=internal_message,
        permission_level="trusted",
        sender_uid="42",
        channel_kind="dm",
    )

    assert comment_reply is None
    assert dm_reply == "收到你的消息了"
    assert internal_message not in dm_reply


@pytest.mark.asyncio
async def test_config_store_recovers_from_invalid_json(tmp_path):
    messages: list[tuple[object, ...]] = []
    logger = SimpleNamespace(warning=lambda *args: messages.append(args))
    store = BiliDMConfigStore(tmp_path, logger=logger)
    store.path.write_text("{invalid", encoding="utf-8")

    loaded = await store.load()

    assert loaded == store.default_config()
    assert messages


@pytest.mark.asyncio
async def test_legacy_manifest_values_migrate_only_when_data_file_is_missing(tmp_path):
    plugin = object.__new__(BiliDMPlugin)
    plugin.config_store = BiliDMConfigStore(tmp_path)
    plugin._settings = plugin.config_store.default_config()
    messages: list[str] = []
    plugin.logger = SimpleNamespace(info=messages.append)

    migrated = await plugin._load_business_config(
        {
            "sesdata": "legacy-secret",
            "dedeuserid": "42",
            "permission_mode": "open",
        }
    )
    assert migrated["sesdata"] == "legacy-secret"
    assert migrated["dedeuserid"] == "42"
    assert messages

    retained = await plugin._load_business_config({"sesdata": "must-not-overwrite"})
    assert retained["sesdata"] == "legacy-secret"


def test_dashboard_never_returns_cookie_values():
    plugin = object.__new__(BiliDMPlugin)
    plugin.ctx = SimpleNamespace(plugin_id="bilibili_dm")
    plugin._settings = {
        **BiliDMConfigStore(Path(".")).default_config(),
        "sesdata": "sess-secret",
        "bili_jct": "csrf-secret",
        "buvid3": "buvid-secret",
        "dedeuserid": "123456789",
        "ac_time_value": "refresh-secret",
    }
    plugin._running = False
    plugin._permission_mode = "allow_list"
    plugin._max_concurrent_messages = 3
    plugin._ai_connect_timeout_seconds = 10.0
    plugin._ai_turn_timeout_seconds = 60.0
    plugin._handler_shutdown_timeout_seconds = 10.0
    plugin.permission_mgr = PermissionManager([])

    state = plugin._build_dashboard_state()
    serialized = json.dumps(state, ensure_ascii=False)

    assert state["status"]["credentials_configured"] is True
    assert state["credentials"]["dedeuserid_masked"] == "123***789"
    for secret in (
        "sess-secret",
        "csrf-secret",
        "buvid-secret",
        "123456789",
        "refresh-secret",
    ):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_panel_settings_preserve_omitted_credentials(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._settings = await plugin.config_store.save(
        {
            "sesdata": "existing-secret",
            "bili_jct": "existing-csrf",
            "permission_mode": "allow_list",
        }
    )

    result = await plugin.save_settings(
        permission_mode="open", max_concurrent_messages=7
    )

    assert isinstance(result, Ok)
    reloaded = await plugin.config_store.load()
    assert reloaded["sesdata"] == "existing-secret"
    assert reloaded["bili_jct"] == "existing-csrf"
    assert reloaded["permission_mode"] == "open"
    assert reloaded["max_concurrent_messages"] == 7
    assert "existing-secret" not in json.dumps(result.value)


@pytest.mark.asyncio
async def test_legacy_trusted_users_are_persisted_to_store(tmp_path):
    plugin = make_plugin(tmp_path)
    persisted: dict[str, object] = {}

    class Store:
        async def get(self, key):
            assert key == "trusted_users"
            return Ok(None)

        async def set(self, key, value):
            persisted[key] = value
            return Ok(True)

    plugin.store = Store()

    await plugin._initialize_permissions(
        {"trusted_users": [{"uid": "42", "level": "admin", "nickname": "legacy"}]}
    )

    assert plugin.permission_mgr.is_admin("42")
    assert persisted["trusted_users"] == [
        {"uid": "42", "level": "admin", "nickname": "legacy"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credentials",
    ({}, {"sesdata": "sess-secret"}, {"bili_jct": "csrf-secret"}),
)
async def test_listener_rejects_incomplete_required_credentials(tmp_path, credentials):
    plugin = make_plugin(tmp_path)
    await plugin.config_store.save(credentials)

    result = await plugin.start_listening()

    assert isinstance(result, Err)
    assert plugin._running is False


@pytest.mark.asyncio
async def test_clear_credentials_serializes_with_listener_start(tmp_path):
    plugin = make_plugin(tmp_path)
    await plugin.config_store.save(
        {"sesdata": "sess-secret", "bili_jct": "csrf-secret"}
    )
    connect_entered = asyncio.Event()
    allow_connect = asyncio.Event()

    class Client:
        def __init__(self):
            self.disconnect_calls = 0

        async def connect(self):
            connect_entered.set()
            await allow_connect.wait()

        async def disconnect(self):
            self.disconnect_calls += 1

        async def receive_message(self, timeout=1.0):
            await asyncio.sleep(timeout)
            return None

    client = Client()
    plugin._create_bili_client = lambda: setattr(plugin, "bili_client", client)

    start_task = asyncio.create_task(plugin.start_listening())
    await connect_entered.wait()
    clear_task = asyncio.create_task(plugin.clear_credentials())
    await asyncio.sleep(0)
    assert not clear_task.done()

    allow_connect.set()
    start_result, clear_result = await asyncio.gather(start_task, clear_task)

    assert isinstance(start_result, Ok)
    assert isinstance(clear_result, Ok)
    assert plugin._running is False
    assert client.disconnect_calls == 1
    reloaded = await plugin.config_store.load()
    assert reloaded["sesdata"] == ""
    assert reloaded["bili_jct"] == ""


def test_manifest_registers_panel_without_credential_defaults():
    manifest_path = Path(__file__).parents[1] / "plugin.toml"
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["plugin"]["ui"]["enabled"] is True
    assert manifest["plugin"]["ui"]["panel"][0]["entry"] == "static/index.html"
    assert "bilibili_dm" not in manifest


def test_static_ui_assets_are_versioned_and_not_cached():
    plugin_source = (Path(__file__).parents[1] / "__init__.py").read_text(
        encoding="utf-8"
    )
    page = (Path(__file__).parents[1] / "static" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'cache_control="no-cache, no-store, must-revalidate"' in plugin_source
    assert 'UI_ASSET_VERSION = "1.2.0"' in plugin_source
    assert "style.css?v=1.2.0" in page
    assert "i18n.js?v=1.2.0" in page
    assert "script.js?v=1.2.0" in page


def test_qr_login_panel_can_be_cancelled_and_auto_closes_after_success():
    plugin_source = (Path(__file__).parents[1] / "__init__.py").read_text(
        encoding="utf-8"
    )
    page = (Path(__file__).parents[1] / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    script = (Path(__file__).parents[1] / "static" / "script.js").read_text(
        encoding="utf-8"
    )

    assert 'id="btn-qr-cancel"' in page
    assert "cancel_qr_login" in plugin_source
    assert "sessionId: null" in script
    assert "qrClientId" in script
    assert "request_generation: generation" in script
    assert "await callPlugin('cancel_qr_login', { session_id: sessionId })" in script
    assert (
        "await callPlugin('poll_qr_login', { session_id: qrLogin.sessionId })" in script
    )
    assert "closeTimer: null" in script
    assert "if (qrLogin.closeTimer) clearTimeout(qrLogin.closeTimer);" in script
    assert "qrLogin.closeTimer = setTimeout" in script
    assert "const completionGeneration = qrLogin.generation;" in script
    assert "const closeAt = Date.now() + 2000;" in script
    assert "if (completionGeneration !== qrLogin.generation) return;" in script
    assert "Math.max(0, closeAt - Date.now())" in script
    assert script.index("qrLogin.closeTimer = setTimeout") < script.index(
        "await refreshDashboard(true)"
    )
    assert "data.status === 'no_session' || data.status === 'cancelled'" in script
    assert "扫码登录已结束，请重新获取二维码" in script
