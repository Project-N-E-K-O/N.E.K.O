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

"""R11 identity-scope forensics: the probe line.

The fail-closed alarm lives in the qq_auto_reply plugin's message dispatcher;
its tests left this repository with the plugin. The probe itself is part of
``utils.connection.qq.open_platform`` and is tested here.

Both subjects under test are pure observation.  The point of these tests is
as much to pin what they must NOT do (leak chat content, change a permission
decision, break the receive loop) as what they must.

See ``docs/design/speaker-trust-entity-semantics.md`` section 2.15.4.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from utils.connection.qq import open_platform as open_plat_mod
from utils.connection.qq.open_platform import (
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
    connection._emit_log = MagicMock()
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
async def test_probe_reaches_both_the_log_file_and_the_in_app_log_page():
    # File-only would be invisible in the UI: get_recent_logs falls back to the
    # log file only when the in-memory ring is EMPTY, and the ring is never
    # empty (startup lines + one per message).  Meanwhile the neighbouring
    # accounts hint tells the user their ID "can be seen in the logs".
    connection = _make_connection(probe_enabled=True, frames=[
        _dispatch("GROUP_AT_MESSAGE_CREATE", GROUP_EVENT),
    ])

    await asyncio.wait_for(connection._receive_loop(), timeout=5.0)

    file_lines = _probe_lines(connection)
    ring_calls = connection._emit_log.call_args_list
    assert len(file_lines) == 1
    assert len(ring_calls) == 1
    assert ring_calls[0].args == ("INFO", file_lines[0])


def test_connection_without_an_emit_log_sink_still_works():
    # The napcat client defaults emit_log to a no-op; mirror that so a caller
    # that only wants the file sink cannot crash the receive loop.
    connection = QQOpenPlatformConnection(app_id="a", client_secret="b")
    connection.logger = MagicMock()

    # Called directly, i.e. OUTSIDE _log_identity_probe's catch-all -- a None
    # sink would raise here.  Asserting through the catch-all would prove
    # nothing: the file write happens first, so the exception is invisible.
    connection._write_identity_probe("[R11] whatever")

    assert connection.logger.info.call_args.args == ("[R11] whatever",)


@pytest.mark.asyncio
async def test_cap_notice_does_not_promise_a_reset_that_never_happens(monkeypatch):
    # The counter lives on the connection object, and qq_client is only rebuilt
    # when the *connection mode* changes -- the sidebar's stop/start does not
    # touch it.  Telling the user to restart auto-reply would be a lie.
    monkeypatch.setattr(open_plat_mod, "_IDENTITY_PROBE_MAX_LINES", 1)
    connection = _make_connection(probe_enabled=True, frames=[
        _dispatch("C2C_MESSAGE_CREATE", {"author": {"id": f"ID_{i}"}})
        for i in range(3)
    ])

    await asyncio.wait_for(connection._receive_loop(), timeout=5.0)

    notice = _probe_lines(connection)[1]
    assert "重启应用" in notice
    assert "自动回复" not in notice


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


# ==========================================================================
# C. The user-facing copy
# ==========================================================================


#: Words that mean something only to whoever read the design doc.  The first
#: version of this copy shipped "[R11]" and "取证" to end users; this list is
#: what stops that happening again.  Deliberately case-insensitive and
#: substring-matched.


#: Implementation detail that has no meaning to the person reading the panel.
#: Two rounds of this copy shipped "字段名" and a "200 行" cap before the
#: maintainer asked, twice, what the sentence was even supposed to mean.
