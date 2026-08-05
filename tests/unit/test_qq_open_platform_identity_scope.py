# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""R11 identity-scope forensics: the probe line and the fail-closed alarm.

Both subjects under test are pure observation.  The point of these tests is
as much to pin what they must NOT do (leak chat content, change a permission
decision, break the receive loop) as what they must.

See ``docs/design/speaker-trust-entity-semantics.md`` section 2.15.4.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugin.plugins.qq_auto_reply import message_dispatcher as dispatcher_mod
from plugin.plugins.qq_auto_reply import qq_open_plat as open_plat_mod
from plugin.plugins.qq_auto_reply.message_dispatcher import QQMessageDispatcher
from plugin.plugins.qq_auto_reply.qq_open_plat import (
    QQOpenPlatformConnection,
    build_identity_probe_line,
)


# ==========================================================================
# A. The probe line itself
# ==========================================================================

GROUP_EVENT = {
    "id": "MSGID_ABCDEF",
    "content": "<@!1234> 帮我看看这个密码是 hunter2",
    "timestamp": "2026-08-05T10:00:00+08:00",
    "group_openid": "GROUP_OPENID_X",
    "author": {
        "id": "AUTHOR_ID_IN_GROUP_X",
        "member_openid": "MEMBER_OPENID_X",
        "union_openid": "UNION_OPENID_SHARED",
        "username": "张三",
    },
    "attachments": [
        {"url": "https://cdn.example.com/private/photo.jpg", "filename": "photo.jpg"},
    ],
}


def test_probe_line_carries_every_field_the_forensics_needs():
    line = build_identity_probe_line("GROUP_AT_MESSAGE_CREATE", GROUP_EVENT)

    assert line.startswith("[R11] event=GROUP_AT_MESSAGE_CREATE ")
    # (1) author.id, plus (2) the values of its openid siblings, so the
    # maintainer can see which one (if any) is equal across two groups.
    assert '"id": "AUTHOR_ID_IN_GROUP_X"' in line
    assert '"member_openid": "MEMBER_OPENID_X"' in line
    assert '"union_openid": "UNION_OPENID_SHARED"' in line
    # (2b) every sibling key name, including ones nobody anticipated.
    assert '"username"' in line
    # (3) which key the group identifier hangs off, and its value.
    assert '"group_openid": "GROUP_OPENID_X"' in line
    # (3b) the fallback in case the group key name doesn't even say "group".
    assert '"attachments"' in line and '"timestamp"' in line


def test_probe_line_never_carries_chat_content():
    line = build_identity_probe_line("GROUP_AT_MESSAGE_CREATE", GROUP_EVENT)

    # The message body, the attachment URL and the display name are all values
    # of non-identifier fields: their KEYS may appear, their VALUES may not.
    assert "hunter2" not in line
    assert "帮我看看" not in line
    assert "https://cdn.example.com" not in line
    assert "photo.jpg" not in line
    assert "张三" not in line
    # The one substring of the body that is allowed through is none at all --
    # not even the bot mention that lives inside content.
    assert "<@!" not in line


def test_probe_line_reports_c2c_author_id_for_the_fourth_comparison():
    line = build_identity_probe_line("C2C_MESSAGE_CREATE", {
        "id": "MSGID_C2C",
        "content": "hi",
        "author": {"id": "AUTHOR_ID_IN_C2C", "user_openid": "USER_OPENID_1"},
    })

    assert '"id": "AUTHOR_ID_IN_C2C"' in line
    assert '"user_openid": "USER_OPENID_1"' in line
    # No group on a C2C event -- the slot must still be present and empty so
    # the two event kinds line up when eyeballed side by side.
    assert "group.ids={}" in line


@pytest.mark.parametrize("data", [None, "", 42, [], {"author": "not-a-dict"}])
def test_probe_line_survives_malformed_payloads(data):
    line = build_identity_probe_line("C2C_MESSAGE_CREATE", data)
    assert line.startswith("[R11] event=C2C_MESSAGE_CREATE ")


def test_probe_line_truncates_absurd_identifier_values():
    huge = "Z" * (open_plat_mod._IDENTITY_PROBE_VALUE_MAX_CHARS + 500)
    line = build_identity_probe_line("C2C_MESSAGE_CREATE", {"author": {"id": huge}})

    assert "Z" * open_plat_mod._IDENTITY_PROBE_VALUE_MAX_CHARS in line
    assert "Z" * (open_plat_mod._IDENTITY_PROBE_VALUE_MAX_CHARS + 1) not in line


def test_identifier_keys_are_matched_by_shape_not_by_enumeration():
    # The whole point of the forensics is to discover an openid sibling nobody
    # listed in advance, so the picker must not be an allowlist of names.
    assert open_plat_mod._is_identifier_key("some_openid_we_never_heard_of")
    assert open_plat_mod._is_identifier_key("guild_id")
    assert open_plat_mod._is_identifier_key("id")
    assert not open_plat_mod._is_identifier_key("username")
    assert not open_plat_mod._is_identifier_key("content")


# ==========================================================================
# B. The probe inside _receive_loop
# ==========================================================================


class _ScriptedWS:
    """Feeds a fixed list of frames, then stops the loop cleanly."""

    def __init__(self, connection, frames):
        self._connection = connection
        self._frames = list(frames)

    async def recv(self):
        if self._frames:
            return json.dumps(self._frames.pop(0))
        self._connection._closing = True
        return json.dumps({"op": 11})  # heartbeat ack -> continue -> exit


def _make_connection(*, probe_enabled, frames):
    connection = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    connection.logger = MagicMock()
    connection._closing = False
    connection._last_seq = 0
    connection._self_id = ""
    connection._identity_probe = (lambda: probe_enabled)
    connection._identity_probe_emitted = 0
    connection._message_queue = asyncio.Queue(maxsize=64)
    connection._ws = _ScriptedWS(connection, frames)
    connection._convert_event = lambda event_type, data: {"event_type": event_type}
    return connection


def _probe_lines(connection):
    return [call.args[0] for call in connection.logger.info.call_args_list]


def _dispatch(event_type, data):
    return {"op": 0, "s": 1, "t": event_type, "d": data}


@pytest.mark.asyncio
async def test_receive_loop_stays_silent_while_the_probe_is_off():
    connection = _make_connection(probe_enabled=False, frames=[
        _dispatch("GROUP_AT_MESSAGE_CREATE", GROUP_EVENT),
        _dispatch("C2C_MESSAGE_CREATE", {"author": {"id": "A"}}),
    ])

    await asyncio.wait_for(connection._receive_loop(), timeout=5.0)

    assert connection.logger.info.call_args_list == []
    # ...and the messages still flowed.
    assert connection._message_queue.qsize() == 2


@pytest.mark.asyncio
async def test_receive_loop_logs_both_event_kinds_and_nothing_else():
    connection = _make_connection(probe_enabled=True, frames=[
        _dispatch("GROUP_AT_MESSAGE_CREATE", GROUP_EVENT),
        _dispatch("C2C_MESSAGE_CREATE", {"author": {"id": "AUTHOR_ID_IN_C2C"}}),
        _dispatch("GUILD_MEMBER_ADD", {"author": {"id": "IRRELEVANT"}}),
    ])

    await asyncio.wait_for(connection._receive_loop(), timeout=5.0)

    lines = _probe_lines(connection)
    assert len(lines) == 2
    assert "event=GROUP_AT_MESSAGE_CREATE" in lines[0]
    assert "AUTHOR_ID_IN_GROUP_X" in lines[0]
    assert "event=C2C_MESSAGE_CREATE" in lines[1]
    assert "AUTHOR_ID_IN_C2C" in lines[1]


@pytest.mark.asyncio
async def test_probe_is_read_per_event_so_the_switch_needs_no_reconnect():
    enabled = {"value": False}
    connection = _make_connection(probe_enabled=False, frames=[
        _dispatch("C2C_MESSAGE_CREATE", {"author": {"id": "BEFORE"}}),
        _dispatch("C2C_MESSAGE_CREATE", {"author": {"id": "AFTER"}}),
    ])
    connection._identity_probe = lambda: enabled["value"]
    original = connection._convert_event

    def _flip(event_type, data):
        enabled["value"] = True
        return original(event_type, data)

    connection._convert_event = _flip

    await asyncio.wait_for(connection._receive_loop(), timeout=5.0)

    lines = _probe_lines(connection)
    assert len(lines) == 1 and "AFTER" in lines[0]


@pytest.mark.asyncio
async def test_probe_stops_at_the_cap_with_exactly_one_notice(monkeypatch):
    monkeypatch.setattr(open_plat_mod, "_IDENTITY_PROBE_MAX_LINES", 3)
    connection = _make_connection(probe_enabled=True, frames=[
        _dispatch("C2C_MESSAGE_CREATE", {"author": {"id": f"ID_{i}"}})
        for i in range(10)
    ])

    await asyncio.wait_for(connection._receive_loop(), timeout=5.0)

    lines = _probe_lines(connection)
    assert len(lines) == 4  # 3 forensic lines + 1 cap notice
    assert all("event=C2C_MESSAGE_CREATE" in line for line in lines[:3])
    assert "event=" not in lines[3]
    # ...and the traffic itself was never held back by the cap.
    assert connection._message_queue.qsize() == 10


@pytest.mark.asyncio
async def test_probe_failure_never_costs_a_reconnect():
    # _receive_loop's catch-all except treats any exception as a disconnect.
    # A diagnostic log line is not allowed to trigger one.
    connection = _make_connection(probe_enabled=True, frames=[
        _dispatch("C2C_MESSAGE_CREATE", {"author": {"id": "A"}}),
    ])
    connection.logger.info.side_effect = RuntimeError("log sink exploded")
    connection._try_reconnect = MagicMock(
        side_effect=AssertionError("reconnected because of a log line"),
    )

    await asyncio.wait_for(connection._receive_loop(), timeout=5.0)

    assert connection._message_queue.qsize() == 1


def test_probe_defaults_to_off_when_no_switch_is_wired():
    connection = QQOpenPlatformConnection(app_id="a", client_secret="b")
    assert connection._identity_probe_enabled() is False


def test_plugin_wires_the_switch_from_settings():
    # The connector reads the flag through a callable, so flipping the setting
    # takes effect on the next event rather than on the next reconnect.
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    plugin = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    plugin.logger = MagicMock()
    plugin._qq_settings = {
        "qq_connection_mode": "open_platform",
        "qq_open_app_id": "app",
        "qq_open_client_secret": "secret",
        "qq_open_identity_probe_enabled": False,
    }

    connection = plugin._make_qq_connection()
    assert connection._identity_probe_enabled() is False
    plugin._qq_settings["qq_open_identity_probe_enabled"] = True
    assert connection._identity_probe_enabled() is True


def test_probe_setting_defaults_to_off_on_disk(tmp_path):
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    defaults = QQAutoReplyConfigStore(tmp_path).default_config()
    assert defaults["qq_open_identity_probe_enabled"] is False


def _full_stack_plugin(tmp_path):
    """A plugin fake wired with the REAL dashboard and settings services.

    Only the disk write is stubbed.  Everything else -- entry -> dashboard ->
    settings -> `_qq_settings` -- runs for real, which is the point: each of
    those layers has its own explicit parameter list and drops what it doesn't
    name.
    """
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore
    from plugin.plugins.qq_auto_reply.dashboard_service import QQDashboardService
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    store = QQAutoReplyConfigStore(tmp_path)
    runtime_status = {
        "napcat_managed": False,
        "napcat_running": False,
        "recent_pipeline_traces": [],
    }
    plugin = SimpleNamespace(
        _qq_settings=store.default_config(),
        _user_sessions={},
        _running=False,
        _startup_error=None,
        _relay_backlog_items=[],
        _mask_token=lambda token: "***",
        _normal_relay_probability=0.1,
        _truth_reply_probability=0.1,
        _ensure_qq_client_initialized=MagicMock(),
        _spawn_memory_sync_task=MagicMock(),
        logger=MagicMock(),
        _emit_log=MagicMock(),
        config_store=store,
        attention_service=None,
        qq_client=None,
        permission_mgr=None,
        group_permission_mgr=None,
        plugin_id="qq_auto_reply",
        i18n=SimpleNamespace(t=lambda _key, default="": default),
        napcat_service=SimpleNamespace(get_napcat_directory=lambda: tmp_path),
        runtime_service=SimpleNamespace(
            fetch_login_status_payload=_async_value({}),
            build_runtime_status=lambda: dict(runtime_status),
        ),
    )
    plugin.settings_service = QQSettingsService(plugin)
    plugin.settings_service.persist_business_config = AsyncMock(return_value=True)
    plugin.dashboard_service = QQDashboardService(plugin)
    return plugin


def _async_value(value):
    async def _call():
        return value

    return _call


@pytest.mark.asyncio
async def test_the_switch_survives_every_layer_of_the_save_path(tmp_path):
    # The flag has to be declared and forwarded at four layers -- entry schema,
    # entry, dashboard, settings branch -- and every one of them drops what it
    # doesn't name.  Miss any single layer and the checkbox looks like it saves
    # while the connector never sees it.
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
    from plugin.sdk.shared.core.decorators import EVENT_META_ATTR

    schema = getattr(QQAutoReplyPlugin.save_settings, EVENT_META_ATTR).input_schema
    assert schema["additionalProperties"] is False  # unnamed keys are rejected
    assert schema["properties"]["qq_open_identity_probe_enabled"] == {
        "type": "boolean",
    }

    plugin = _full_stack_plugin(tmp_path)
    assert plugin._qq_settings["qq_open_identity_probe_enabled"] is False

    await QQAutoReplyPlugin.save_settings(
        plugin, qq_open_identity_probe_enabled=True,
    )
    assert plugin._qq_settings["qq_open_identity_probe_enabled"] is True
    await QQAutoReplyPlugin.save_settings(
        plugin, qq_open_identity_probe_enabled=False,
    )
    assert plugin._qq_settings["qq_open_identity_probe_enabled"] is False
    # A save that doesn't mention the flag must not silently reset it.
    plugin._qq_settings["qq_open_identity_probe_enabled"] = True
    await QQAutoReplyPlugin.save_settings(plugin, sticker_cooldown_messages=5)
    assert plugin._qq_settings["qq_open_identity_probe_enabled"] is True


@pytest.mark.asyncio
async def test_dashboard_hands_the_probe_flag_back(tmp_path):
    # Without this the checkbox would reopen unticked every time, and the
    # maintainer would have no way to tell whether the probe is on.
    plugin = _full_stack_plugin(tmp_path)
    plugin._qq_settings["qq_open_identity_probe_enabled"] = True

    state = await plugin.dashboard_service.build_dashboard_state()
    assert state["settings"]["qq_open_identity_probe_enabled"] is True


# ==========================================================================
# C. The fail-closed alarm
# ==========================================================================


def _make_dispatcher(*, roster):
    dispatcher = QQMessageDispatcher.__new__(QQMessageDispatcher)
    dispatcher.plugin = SimpleNamespace(
        logger=MagicMock(),
        _emit_log=MagicMock(),
        permission_mgr=SimpleNamespace(list_users=lambda: list(roster)),
    )
    return dispatcher


ADMIN_ROSTER = [{"qq": "OWNER_PRIVATE_OPENID", "level": "admin"}]


def _speak(dispatcher, *, sender, level="none", group="GROUP_X", channel="open"):
    message = {
        "message_type": "group",
        "channel": channel,
        "group_id": group,
        "user_id": sender,
    }
    dispatcher._note_open_platform_identity_scope(message, level)
    return message


def _warnings(dispatcher):
    return [call.args[0] for call in dispatcher.plugin.logger.warning.call_args_list]


def test_alarm_stays_quiet_below_the_distinct_speaker_threshold():
    dispatcher = _make_dispatcher(roster=ADMIN_ROSTER)
    for i in range(QQMessageDispatcher.OPEN_PLATFORM_SCOPE_ALARM_SPEAKERS - 1):
        _speak(dispatcher, sender=f"STRANGER_{i}")
    assert _warnings(dispatcher) == []


def test_alarm_ignores_a_single_chatty_stranger():
    dispatcher = _make_dispatcher(roster=ADMIN_ROSTER)
    for _ in range(20):
        _speak(dispatcher, sender="ONE_TALKATIVE_STRANGER")
    assert _warnings(dispatcher) == []


def test_alarm_fires_once_per_group_and_names_the_group():
    dispatcher = _make_dispatcher(roster=ADMIN_ROSTER)
    for i in range(QQMessageDispatcher.OPEN_PLATFORM_SCOPE_ALARM_SPEAKERS + 5):
        _speak(dispatcher, sender=f"STRANGER_{i}")

    warnings = _warnings(dispatcher)
    assert len(warnings) == 1
    assert "[R11]" in warnings[0] and "GROUP_X" in warnings[0]
    # The same text also reaches the plugin's in-app log page.
    assert dispatcher.plugin._emit_log.call_args.args == ("WARN", warnings[0])


def test_alarm_tracks_groups_independently():
    dispatcher = _make_dispatcher(roster=ADMIN_ROSTER)
    for group in ("GROUP_X", "GROUP_Y"):
        for i in range(QQMessageDispatcher.OPEN_PLATFORM_SCOPE_ALARM_SPEAKERS):
            _speak(dispatcher, sender=f"{group}_STRANGER_{i}", group=group)

    warnings = _warnings(dispatcher)
    assert len(warnings) == 2
    assert any("GROUP_X" in text for text in warnings)
    assert any("GROUP_Y" in text for text in warnings)


def test_one_recognized_speaker_silences_that_group_forever():
    # A match proves the group-side ids share the roster's scope: R11 is not
    # manifesting here, so the alarm must never fire in this group again.
    dispatcher = _make_dispatcher(roster=ADMIN_ROSTER)
    _speak(dispatcher, sender="STRANGER_0")
    _speak(dispatcher, sender="OWNER_PRIVATE_OPENID", level="admin")
    for i in range(1, 40):
        _speak(dispatcher, sender=f"STRANGER_{i}")

    assert _warnings(dispatcher) == []


@pytest.mark.parametrize("level", ["admin", "trusted", "normal"])
def test_any_registered_level_counts_as_a_match(level):
    dispatcher = _make_dispatcher(roster=ADMIN_ROSTER)
    _speak(dispatcher, sender="KNOWN", level=level)
    for i in range(40):
        _speak(dispatcher, sender=f"STRANGER_{i}")
    assert _warnings(dispatcher) == []


def test_alarm_never_fires_on_the_napcat_channel():
    dispatcher = _make_dispatcher(roster=ADMIN_ROSTER)
    for i in range(40):
        _speak(dispatcher, sender=f"STRANGER_{i}", channel="napcat")
    assert _warnings(dispatcher) == []


def test_alarm_channel_constant_matches_the_connector():
    assert dispatcher_mod._OPEN_PLATFORM_CHANNEL == QQOpenPlatformConnection.CHANNEL


@pytest.mark.parametrize("roster", [
    [],
    [{"qq": "SOMEONE", "level": "trusted"}],
    [{"qq": "SOMEONE", "level": "normal"}],
])
def test_alarm_needs_a_registered_admin_to_be_meaningful(roster):
    # With nobody claiming ownership, "the group recognizes no one" is the
    # expected state, not a symptom.
    dispatcher = _make_dispatcher(roster=roster)
    for i in range(40):
        _speak(dispatcher, sender=f"STRANGER_{i}")
    assert _warnings(dispatcher) == []


def test_alarm_bounds_how_many_groups_it_tracks():
    dispatcher = _make_dispatcher(roster=ADMIN_ROSTER)
    limit = QQMessageDispatcher.OPEN_PLATFORM_SCOPE_ALARM_MAX_GROUPS
    for group_index in range(limit + 20):
        _speak(dispatcher, sender="STRANGER", group=f"GROUP_{group_index}")

    assert len(dispatcher._open_platform_scope_alarm_state) == limit


def test_alarm_changes_no_permission_state():
    permission_mgr = MagicMock()
    permission_mgr.list_users.return_value = list(ADMIN_ROSTER)
    dispatcher = QQMessageDispatcher.__new__(QQMessageDispatcher)
    dispatcher.plugin = SimpleNamespace(
        logger=MagicMock(),
        _emit_log=MagicMock(),
        permission_mgr=permission_mgr,
    )

    seen = []
    for i in range(QQMessageDispatcher.OPEN_PLATFORM_SCOPE_ALARM_SPEAKERS + 2):
        seen.append(_speak(dispatcher, sender=f"STRANGER_{i}"))

    assert _warnings(dispatcher)  # the alarm did fire...
    permission_mgr.add_user.assert_not_called()
    permission_mgr.remove_user.assert_not_called()
    permission_mgr.get_permission_level.assert_not_called()
    # ...and not one message dict grew a key on the way through.
    for message in seen:
        assert set(message) == {"message_type", "channel", "group_id", "user_id"}


def test_alarm_survives_a_broken_permission_manager():
    dispatcher = QQMessageDispatcher.__new__(QQMessageDispatcher)
    dispatcher.plugin = SimpleNamespace(
        logger=MagicMock(),
        _emit_log=MagicMock(),
        permission_mgr=SimpleNamespace(
            list_users=MagicMock(side_effect=RuntimeError("roster on fire")),
        ),
    )
    _speak(dispatcher, sender="STRANGER")  # must not raise
    assert _warnings(dispatcher) == []


@pytest.mark.asyncio
async def test_process_messages_feeds_the_alarm_the_level_it_actually_stamped():
    inbox = [{
        "message_type": "group",
        "channel": "open",
        "group_id": "GROUP_X",
        "user_id": "STRANGER_0",
    }]
    stamped: list[dict] = []

    async def _receive():
        if inbox:
            return inbox.pop(0)
        plugin._running = False
        return None

    async def _handle(message):
        stamped.append(message)

    plugin = SimpleNamespace(
        _running=True,
        _qq_settings={},
        qq_client=SimpleNamespace(receive_message=_receive),
        logger=MagicMock(),
        _emit_log=MagicMock(),
        _run_message_handler=_handle,
        handler_runtime_service=SimpleNamespace(track_handler_task=lambda task: None),
        permission_mgr=SimpleNamespace(
            get_permission_level=lambda _sender: "none",
            list_users=lambda: list(ADMIN_ROSTER),
        ),
    )
    dispatcher = QQMessageDispatcher.__new__(QQMessageDispatcher)
    dispatcher.plugin = plugin
    observed: list[tuple[dict, object]] = []
    dispatcher._note_open_platform_identity_scope = (
        lambda message, level: observed.append((message, level))
    )

    await asyncio.wait_for(dispatcher.process_messages(), timeout=5.0)
    await asyncio.sleep(0)  # let the handler task the loop spawned finish

    assert stamped, "the handler was never scheduled"
    assert len(observed) == 1
    message, level = observed[0]
    assert level == message["_group_speaker_permission_level_at_receipt"] == "none"
