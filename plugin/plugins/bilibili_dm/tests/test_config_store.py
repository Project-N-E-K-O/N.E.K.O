from __future__ import annotations

import asyncio
import json
from collections import OrderedDict, deque
from pathlib import Path
from types import SimpleNamespace
import tomllib
from unittest.mock import AsyncMock, MagicMock

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
    plugin._session_lock_refs = {}
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
    client._notification_feed_seen = {"reply": [], "at": []}
    client._notification_feed_seen_set = {"reply": set(), "at": set()}
    assert client._mark_notification_seen("reply", notification) is True
    assert client._mark_notification_seen("reply", notification) is False
    duplicate_from_at = {**notification, "id": 202}
    assert client._mark_notification_seen("at", duplicate_from_at) is False


def test_notification_is_completed_only_after_delivery_ack():
    client = BiliDMClient(sesdata="sess", bili_jct="csrf")
    notification = {
        "id": 101,
        "item": {
            "business_id": 1,
            "subject_id": 987654,
            "source_id": 12,
        },
    }
    identity = client._notification_identity(notification)

    assert client._mark_notification_seen("reply", notification) is True
    assert identity in client._notification_inflight
    assert identity not in client._notification_seen_set

    client.retry_comment_notification(
        {
            "notification_identity": identity,
            "notification_attempt": 0,
            "sender_uid": "42",
        }
    )
    client._notification_retries[identity]["ready_at"] = 0
    assert client._drain_notification_retries() == 1
    assert client._message_queue.get_nowait()["notification_attempt"] == 1
    assert identity not in client._notification_seen_set

    client.complete_comment_notification(identity)
    assert identity not in client._notification_inflight
    assert identity in client._notification_seen_set


def test_notification_retry_attempts_and_queue_are_bounded():
    client = BiliDMClient(sesdata="sess", bili_jct="csrf")
    client._notification_backlog_limit = 2

    for identity in ("oldest", "newer", "newest"):
        client._notification_inflight.add(identity)
    client._notification_retries = {
        "oldest": {"message": {}, "ready_at": 1},
        "newer": {"message": {}, "ready_at": 2},
    }

    client.retry_comment_notification(
        {"notification_identity": "newest", "notification_attempt": 0}
    )

    assert set(client._notification_retries) == {"newer", "newest"}
    assert "oldest" not in client._notification_inflight
    assert "oldest" in client._notification_seen_set

    client.retry_comment_notification(
        {
            "notification_identity": "newest",
            "notification_attempt": client.NOTIFICATION_MAX_RETRY_ATTEMPTS,
        }
    )

    assert "newest" not in client._notification_retries
    assert "newest" not in client._notification_inflight
    assert "newest" in client._notification_seen_set


def test_comment_retry_blocks_later_messages_in_the_same_thread():
    client = BiliDMClient(sesdata="sess", bili_jct="csrf")
    older = {
        "notification_identity": "comment:old",
        "notification_attempt": 0,
        "conversation_key": "comment:1:2:3",
    }
    newer = {
        "notification_identity": "comment:new",
        "notification_attempt": 0,
        "conversation_key": "comment:1:2:3",
    }
    client._notification_inflight.update(
        {older["notification_identity"], newer["notification_identity"]}
    )

    client.retry_comment_notification(older)
    older_ready_at = client._notification_retries["comment:old"]["ready_at"]

    assert client.defer_comment_notification_behind_retry(newer) is True
    assert client._notification_blocked_conversations == {
        "comment:1:2:3": "comment:old"
    }
    assert client._notification_retries["comment:new"]["ready_at"] > older_ready_at

    client.complete_comment_notification("comment:old")

    assert "comment:new" in client._notification_retries
    assert (
        client._notification_retries["comment:new"]["message"]["notification_identity"]
        == "comment:new"
    )
    assert client.defer_comment_notification_behind_retry(newer) is False


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
    client._notification_feed_seen = {"reply": [], "at": []}
    client._notification_feed_seen_set = {"reply": set(), "at": set()}
    client._notification_pending = deque()
    client._notification_backlog_limit = 500
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

    async def get_items(_http_client, url, *, paginate=False):
        del paginate
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
async def test_notification_feeds_are_merged_by_event_time():
    client = object.__new__(BiliDMClient)
    client.logger = SimpleNamespace(warning=lambda *_: None)
    client._notification_bootstrap_done = {"reply": True, "at": True}
    client._notification_seen = []
    client._notification_seen_set = set()
    client._notification_feed_seen = {"reply": [], "at": []}
    client._notification_feed_seen_set = {"reply": set(), "at": set()}
    client._notification_pending = deque()
    client._notification_backlog_limit = 500
    client._notification_max_items = 2
    client._current_uid = "999"
    client._enqueue_comment_notification = AsyncMock()
    client._is_at_current_user = lambda _: True

    reply_newer = {"id": "reply-newer", "reply_time": 30}
    at_older = {"id": "at-older", "at_time": 20}

    async def get_items(_http_client, url, *, paginate=False):
        del paginate
        return [reply_newer] if url.endswith("/reply") else [at_older]

    client._get_notification_items = get_items

    await client._poll_comment_notifications()

    assert [
        args.args for args in client._enqueue_comment_notification.await_args_list
    ] == [
        (at_older, "at"),
        (reply_newer, "reply"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "time_param"),
    (
        ("https://api.bilibili.com/x/msgfeed/reply", "reply_time"),
        ("https://api.bilibili.com/x/msgfeed/at", "at_time"),
    ),
)
async def test_notification_feed_pages_until_seen_item(url, time_param):
    def item(event_id, source_id):
        return {
            "id": event_id,
            "item": {
                "business_id": 1,
                "subject_id": 99,
                "source_id": source_id,
            },
        }

    newest = item("event-newest", 303)
    newer = item("event-newer", 302)
    seen = item("event-seen", 301)
    seen_only_in_other_feed = item("event-cross-feed", 304)

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeHttpClient:
        def __init__(self):
            self.params = []
            self.responses = deque(
                [
                    FakeResponse(
                        {
                            "code": 0,
                            "data": {
                                "items": [newest, seen_only_in_other_feed],
                                "cursor": {
                                    "is_end": False,
                                    "id": 10,
                                    time_param: 20,
                                },
                            },
                        }
                    ),
                    FakeResponse(
                        {
                            "code": 0,
                            "data": {
                                "items": [newer, seen],
                                "cursor": {"is_end": True, "id": 9, "time": 19},
                            },
                        }
                    ),
                ]
            )

        async def get(self, _url, *, params, **_kwargs):
            self.params.append(dict(params))
            return self.responses.popleft()

    client = object.__new__(BiliDMClient)
    client._credential = SimpleNamespace(
        sessdata="", bili_jct="", buvid3="", dedeuserid=""
    )
    client.logger = SimpleNamespace(warning=lambda *_: None)
    client._notification_backlog_limit = 500
    client._notification_seen_set = {
        client._notification_identity(seen),
        client._notification_identity(seen_only_in_other_feed),
    }
    source = "reply" if url.endswith("/reply") else "at"
    feed_key = client._notification_feed_identity(seen)
    client._notification_feed_seen = {"reply": [], "at": []}
    client._notification_feed_seen[source] = [feed_key]
    client._notification_feed_seen_set = {"reply": set(), "at": set()}
    client._notification_feed_seen_set[source] = {feed_key}
    http_client = FakeHttpClient()

    result = await client._get_notification_items(http_client, url, paginate=True)

    assert result == [newest, seen_only_in_other_feed, newer]
    assert len(http_client.params) == 2
    assert http_client.params[1] == {
        "build": 0,
        "mobi_app": "web",
        "id": "10",
        time_param: "20",
    }


@pytest.mark.asyncio
async def test_notification_pagination_stops_when_backlog_capacity_is_filled():
    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "code": 0,
                "data": {
                    "items": [{"id": "new-3"}, {"id": "new-2"}],
                    "cursor": {
                        "is_end": False,
                        "id": 10,
                        "reply_time": 20,
                    },
                },
            }

    class FakeHttpClient:
        def __init__(self):
            self.calls = 0

        async def get(self, *_args, **_kwargs):
            self.calls += 1
            return FakeResponse()

    client = object.__new__(BiliDMClient)
    client._credential = SimpleNamespace(
        sessdata="", bili_jct="", buvid3="", dedeuserid=""
    )
    client._notification_backlog_limit = 2
    client._notification_feed_seen_set = {"reply": set(), "at": set()}
    client.logger = None
    http_client = FakeHttpClient()

    result = await client._get_notification_items(
        http_client,
        "https://api.bilibili.com/x/msgfeed/reply",
        paginate=True,
    )

    assert result == [{"id": "new-3"}, {"id": "new-2"}]
    assert http_client.calls == 1


@pytest.mark.asyncio
async def test_force_uid_resolution_revalidates_configured_dedeuserid(monkeypatch):
    client = object.__new__(BiliDMClient)
    client._current_uid = "old-account"
    client._credential = SimpleNamespace(dedeuserid="old-account")
    client.logger = SimpleNamespace(warning=lambda *_: None)
    get_self_info = AsyncMock(return_value={"mid": 456})
    monkeypatch.setattr(
        "plugin.plugins.bilibili_dm.bili_client.get_self_info", get_self_info
    )

    resolved = await client._resolve_current_uid(force=True)

    assert resolved is True
    assert client._current_uid == "456"
    assert client._credential.dedeuserid == "456"
    get_self_info.assert_awaited_once_with(client._credential)


@pytest.mark.asyncio
async def test_notification_backlog_is_bounded_and_drops_oldest():
    client = object.__new__(BiliDMClient)
    warnings = []
    client.logger = SimpleNamespace(warning=warnings.append)
    client._notification_bootstrap_done = {"reply": True, "at": True}
    client._notification_seen = []
    client._notification_seen_set = set()
    client._notification_feed_seen = {"reply": [], "at": []}
    client._notification_feed_seen_set = {"reply": set(), "at": set()}
    client._notification_pending = deque()
    client._notification_backlog_limit = 2
    client._notification_max_items = 1
    client._current_uid = "999"
    client._enqueue_comment_notification = AsyncMock()
    client._is_at_current_user = lambda _: True
    notifications = [{"id": f"new-{index}"} for index in range(3, 0, -1)]

    async def get_items(_http_client, url, *, paginate=False):
        del paginate
        return notifications if url.endswith("/reply") else []

    client._get_notification_items = get_items

    await client._poll_comment_notifications()

    client._enqueue_comment_notification.assert_awaited_once_with(
        notifications[1], "reply"
    )
    assert list(client._notification_pending) == [("reply", notifications[0])]
    dropped_identity = client._notification_identity(notifications[2])
    assert dropped_identity not in client._notification_inflight
    assert dropped_identity in client._notification_seen_set
    assert warnings


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
async def test_dm_failure_restarts_private_polling_without_stopping_comments():
    client = object.__new__(BiliDMClient)
    client._running = True
    client.logger = SimpleNamespace(error=lambda *_: None, warning=lambda *_: None)
    client.SESSION_RESTART_INITIAL_DELAY_SECONDS = 0
    client.SESSION_RESTART_MAX_DELAY_SECONDS = 0
    failed_session = SimpleNamespace(
        start=AsyncMock(side_effect=RuntimeError("dm failed"))
    )

    async def stop_after_reconnect(*, exclude_self):
        assert exclude_self is True
        client._running = False

    recovered_session = SimpleNamespace(
        start=AsyncMock(side_effect=stop_after_reconnect)
    )
    client._session = failed_session
    client._create_session = MagicMock(return_value=recovered_session)

    await client._run_session()

    failed_session.start.assert_awaited_once_with(exclude_self=True)
    client._create_session.assert_called_once_with()
    recovered_session.start.assert_awaited_once_with(exclude_self=True)


def test_at_feed_is_blocked_when_current_uid_cannot_be_resolved():
    client = object.__new__(BiliDMClient)
    client._current_uid = ""

    assert client._is_at_current_user({"item": {"at_details": []}}) is False


@pytest.mark.asyncio
async def test_comment_notification_is_not_enqueued_without_current_uid():
    client = object.__new__(BiliDMClient)
    client._credential = SimpleNamespace(dedeuserid="")
    client._current_uid = ""
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

    assert client._message_queue.empty()


def test_at_feed_without_at_details_is_not_discarded():
    client = object.__new__(BiliDMClient)
    client._current_uid = "999"

    assert client._is_at_current_user({"item": {}}) is True


def test_bilibili_api_transport_explicitly_disables_brotli(monkeypatch):
    client = object.__new__(BiliDMClient)
    client.logger = None
    session = SimpleNamespace(headers={})
    transport = SimpleNamespace(get_wrapped_session=lambda: session)

    monkeypatch.setattr(
        "bilibili_api.utils.network.get_client", lambda: transport
    )

    client._disable_brotli_for_bilibili_api()

    assert session.headers["Accept-Encoding"] == "gzip, deflate"


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
async def test_permission_change_waits_for_in_flight_session_creation(tmp_path):
    plugin = make_plugin(tmp_path)
    session_key = "bili:comment:1:2:3:42"
    session = SimpleNamespace(close=AsyncMock())
    handler_entered = asyncio.Event()
    allow_session_creation = asyncio.Event()

    async def create_session_in_flight():
        session_lock = await plugin._get_session_lock(session_key)
        try:
            async with session_lock:
                handler_entered.set()
                await allow_session_creation.wait()
                plugin._user_sessions[session_key] = {
                    "sender_uid": "42",
                    "session": session,
                }
        finally:
            await plugin._release_session_lock(session_key, session_lock)

    handler_task = asyncio.create_task(create_session_in_flight())
    await handler_entered.wait()
    invalidation_task = asyncio.create_task(plugin._invalidate_user_sessions("42"))
    await asyncio.sleep(0)

    assert not invalidation_task.done()
    allow_session_creation.set()
    await asyncio.gather(handler_task, invalidation_task)

    assert session_key not in plugin._user_sessions
    assert session_key not in plugin._session_locks
    session.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("permission_mode", ("open", "deny_list"))
async def test_unlisted_users_get_effective_trusted_role(tmp_path, permission_mode):
    plugin = make_plugin(tmp_path)
    plugin._permission_mode = permission_mode
    plugin._generate_reply = AsyncMock(return_value=None)

    await plugin._handle_message(
        {
            "sender_uid": "42",
            "sender_nickname": "tester",
            "content": "hello",
        }
    )

    assert plugin._generate_reply.await_args.kwargs["permission_level"] == "trusted"


@pytest.mark.asyncio
async def test_failed_comment_generation_schedules_notification_retry(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._running = True
    plugin.permission_mgr.add_user("42", "trusted")
    plugin._generate_reply = AsyncMock(return_value=None)
    plugin.bili_client = SimpleNamespace(
        retry_comment_notification=MagicMock(),
        complete_comment_notification=MagicMock(),
    )
    message = {
        "sender_uid": "42",
        "sender_nickname": "tester",
        "content": "hello",
        "conversation_key": "comment:1:2:3",
        "reply_target": {"type": 1, "oid": 2, "root": 3, "parent": 3},
        "notification_identity": "comment:1:2:3",
    }

    await plugin._run_message_handler(message)

    plugin.bili_client.retry_comment_notification.assert_called_once_with(message)
    plugin.bili_client.complete_comment_notification.assert_not_called()


@pytest.mark.asyncio
async def test_message_processor_reserves_capacity_before_spawning_handlers(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._running = True
    plugin._max_concurrent_messages = 1
    plugin._message_concurrency = asyncio.Semaphore(1)
    plugin.permission_mgr.add_user("42", "trusted")

    handler_started = asyncio.Event()
    keep_handler_running = asyncio.Event()
    receive_calls = 0

    async def receive_message(*, timeout):
        nonlocal receive_calls
        del timeout
        receive_calls += 1
        if receive_calls == 1:
            return {"sender_uid": "42", "content": "first"}
        return {"sender_uid": "42", "content": "second"}

    async def handle_message(_message):
        handler_started.set()
        await keep_handler_running.wait()
        return True

    plugin.bili_client = SimpleNamespace(receive_message=receive_message)
    plugin._handle_message = handle_message
    processor = asyncio.create_task(plugin._process_messages())

    await handler_started.wait()
    await asyncio.sleep(0)

    # One additional message may be held by the consumer while it waits for a
    # slot, but it must not create another handler task.
    assert receive_calls == 2
    assert len(plugin._handler_tasks) == 1

    processor.cancel()
    await processor

    for task in list(plugin._handler_tasks):
        task.cancel()
    await asyncio.gather(*plugin._handler_tasks, return_exceptions=True)

    assert plugin._message_concurrency.locked() is False


@pytest.mark.asyncio
async def test_comment_retry_checks_existing_reply_before_resending(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin.permission_mgr.add_user("42", "trusted")
    plugin.bili_client = SimpleNamespace(
        comment_reply_exists=AsyncMock(return_value=True),
        send_comment_reply=AsyncMock(),
    )
    target = {"type": 1, "oid": 2, "root": 3, "parent": 3}
    message = {
        "sender_uid": "42",
        "sender_nickname": "tester",
        "content": "hello",
        "conversation_key": "comment:1:2:3",
        "reply_target": target,
        "notification_identity": "comment:1:2:3",
        "notification_attempt": 1,
        "generated_comment_reply": "cached reply",
        "comment_send_started_at": 100,
    }

    completed = await plugin._handle_message(message)

    assert completed is True
    plugin.bili_client.comment_reply_exists.assert_awaited_once_with(
        target, "cached reply", sent_after=100
    )
    plugin.bili_client.send_comment_reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_comment_retry_uses_bilibili_notification_time_as_cutoff(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin.permission_mgr.add_user("42", "trusted")
    plugin._generate_reply = AsyncMock(return_value="generated reply")
    plugin.bili_client = SimpleNamespace(
        comment_reply_exists=AsyncMock(return_value=False),
        send_comment_reply=AsyncMock(return_value={"data": {"rpid": 4}}),
    )
    message = {
        "sender_uid": "42",
        "sender_nickname": "tester",
        "content": "hello",
        "conversation_key": "comment:1:2:3",
        "reply_target": {"type": 1, "oid": 2, "root": 3, "parent": 3},
        "notification_identity": "comment:1:2:3",
        "timestamp": 100,
    }

    assert await plugin._handle_message(message) is True
    assert message["comment_send_started_at"] == 100


@pytest.mark.asyncio
async def test_failed_comment_session_is_discarded_before_retry(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    session_key = BiliDMPlugin._build_session_key("42", "comment:1:2:3")
    session = SimpleNamespace(
        stream_text=AsyncMock(side_effect=RuntimeError("transport failed")),
        close=AsyncMock(),
    )
    plugin._user_sessions[session_key] = {
        "session": session,
        "reply_chunks": [],
        "lock": asyncio.Lock(),
        "permission_level": "trusted",
    }
    config_manager = SimpleNamespace(
        get_character_data=lambda: (
            "Master",
            "Neko",
            None,
            {},
            None,
            {},
            None,
            None,
            None,
        ),
        get_model_api_config=lambda _kind: {},
    )
    monkeypatch.setattr(
        "utils.config_manager.get_config_manager", lambda: config_manager
    )

    reply = await plugin._generate_reply(
        message="hello",
        permission_level="trusted",
        sender_uid="42",
        conversation_key="comment:1:2:3",
        channel_kind="comment",
    )

    assert reply is None
    assert session_key not in plugin._user_sessions
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_reused_session_is_rebuilt_after_permission_change(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    session_key = BiliDMPlugin._build_session_key("42")
    stale_session = SimpleNamespace(close=AsyncMock())
    plugin._user_sessions[session_key] = {
        "session": stale_session,
        "reply_chunks": [],
        "lock": asyncio.Lock(),
        "permission_level": "admin",
        "memory_enabled": True,
    }
    config_manager = SimpleNamespace(
        aensure_region_resolved=AsyncMock(),
        get_character_data=lambda: (
            "Master",
            "Neko",
            None,
            {},
            None,
            {},
            None,
            None,
            None,
        ),
        get_model_api_config=lambda _kind: {},
    )
    monkeypatch.setattr(
        "utils.config_manager.get_config_manager", lambda: config_manager
    )

    class FreshSession:
        def __init__(self, *, on_text_delta, **_kwargs):
            self._on_text_delta = on_text_delta

        async def connect(self, **_kwargs):
            return None

        async def stream_text(self, _message):
            await self._on_text_delta("fresh reply", True)

        async def close(self):
            return None

    monkeypatch.setattr("main_logic.omni_offline_client.OmniOfflineClient", FreshSession)
    plugin._wait_session_response_complete = AsyncMock(return_value=True)

    reply = await plugin._generate_reply(
        message="hello",
        permission_level="trusted",
        sender_uid="42",
    )

    assert reply == "fresh reply"
    stale_session.close.assert_awaited_once()
    assert plugin._user_sessions[session_key]["permission_level"] == "trusted"
    assert plugin._user_sessions[session_key]["memory_enabled"] is False


@pytest.mark.asyncio
async def test_comment_reply_idempotency_check_matches_parent_and_send_time(
    monkeypatch,
):
    from bilibili_api.comment import Comment

    get_sub_comments = AsyncMock(
        return_value={
            "replies": [
                {
                    "mid": 999,
                    "parent": 3,
                    "ctime": 101,
                    "content": {"message": "cached reply"},
                }
            ],
            "page": {"num": 1, "size": 20, "count": 1},
        }
    )
    monkeypatch.setattr(Comment, "get_sub_comments", get_sub_comments)
    client = BiliDMClient(sesdata="sess", bili_jct="csrf", dedeuserid="999")

    exists = await client.comment_reply_exists(
        {"type": 1, "oid": 2, "root": 3, "parent": 3},
        "cached reply",
        sent_after=100,
    )

    assert exists is True
    get_sub_comments.assert_awaited_once_with(page_index=1, page_size=20)


@pytest.mark.asyncio
async def test_idle_session_cleanup_also_reclaims_session_lock(tmp_path):
    plugin = make_plugin(tmp_path)
    session_key = "bili:comment:1:2:3:42"
    session = SimpleNamespace(close=AsyncMock())
    plugin._user_sessions[session_key] = {
        "sender_uid": "42",
        "session": session,
        "memory_enabled": False,
        "last_activity_at": 0.1,
    }
    session_lock = await plugin._get_session_lock(session_key)
    await plugin._release_session_lock(session_key, session_lock)

    await plugin._flush_idle_sessions()

    assert session_key not in plugin._user_sessions
    assert session_key not in plugin._session_locks
    assert session_key not in plugin._session_lock_refs
    session.close.assert_awaited_once()


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

    admin_comment_prompt = await plugin._build_session_instructions(
        **{**args, "permission_level": "admin"}, channel_kind="comment"
    )
    assert "B站UID: 42" not in admin_comment_prompt


def test_video_context_cache_is_bounded_and_keeps_recent_entries():
    client = object.__new__(BiliDMClient)
    client._video_info_cache = OrderedDict()
    client._video_info_cache_limit = 2

    client._cache_video_context(1, {"title": "oldest"})
    client._cache_video_context(2, {"title": "middle"})
    client._cache_video_context(1, {"title": "refreshed"})
    client._cache_video_context(3, {"title": "newest"})

    assert list(client._video_info_cache) == [1, 3]
    assert client._video_info_cache[1]["title"] == "refreshed"


@pytest.mark.asyncio
async def test_dm_queue_eviction_retries_claimed_comment_notification():
    client = object.__new__(BiliDMClient)
    client._message_queue = asyncio.Queue(maxsize=1)
    client._notification_inflight = {"comment:1:2:3"}
    client._notification_retries = {}
    client._notification_seen = []
    client._notification_seen_set = set()
    client._notification_backlog_limit = 500
    client.logger = None
    await client._message_queue.put(
        {
            "sender_uid": "42",
            "notification_identity": "comment:1:2:3",
            "notification_attempt": 0,
        }
    )
    client._get_user_nickname = AsyncMock(return_value="sender")
    event = SimpleNamespace(
        sender_uid=7,
        msg_key=9,
        timestamp=1,
        content="private message",
    )

    await client._enqueue_event(event, "text")

    assert "comment:1:2:3" in client._notification_retries
    assert client._message_queue.get_nowait()["sender_uid"] == "7"


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
