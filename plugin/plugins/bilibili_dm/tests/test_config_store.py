from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

from plugin.plugins.bilibili_dm import BiliDMPlugin
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
    assert "unknown" not in saved

    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["sesdata"] == "sess-secret"
    assert raw["bili_jct"] == "csrf-secret"
    assert await store.load() == saved


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
