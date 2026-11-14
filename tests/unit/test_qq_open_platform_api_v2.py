"""The v2 capability surface of ``QQOpenPlatformConnection``.

Three defects were carried by this connector, and all three failed *silently*:

- the capability flags said voice/ark/full-group reception were impossible or
  possible in ways the API no longer matches, so consumers stopped trying or tried
  things the platform rejects;
- the receive loop recognised two event names and dropped the rest, which looked
  exactly like "the bot is not allowed to receive group messages";
- roughly fifty methods ``return {}``. An empty dict does not fail a caller that only
  checks for an exception, so every unsupported operation *looked* done.

These tests pin the corrected behaviour: flags follow the v2 docs, every dispatch
family goes somewhere, a supported call really hits its endpoint, and an unsupported
one raises with the reason in the message.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from utils.connection.qq.open_platform import (
    QQOpenPlatformAPIError,
    QQOpenPlatformConnection,
    QQOpenPlatformUnsupportedError,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


class _Http:
    """Replays scripted answers and records every request the connector makes."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[tuple[str, str, object]] = []

    async def request(self, method, url, headers=None, json=None):
        self.calls.append((method, url, json))
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return _Response({"id": f"msg{len(self.calls)}"})

    # the older inline paths still use httpx-style helpers
    async def post(self, url, json=None, headers=None):
        return await self.request("POST", url, headers, json)


def _conn(script=None) -> QQOpenPlatformConnection:
    conn = QQOpenPlatformConnection(app_id="1", client_secret="2")
    conn._http = _Http(script)
    conn._access_token = "t"
    conn._token_expires_at = float("inf")
    return conn


def run(coro):
    return asyncio.run(coro)


# ── flags ──────────────────────────────────────────────────────────────────

def test_capability_flags_follow_the_v2_surface():
    conn = _conn()
    assert conn.supports_voice is True          # rich media accepts file_type=3
    assert conn.supports_ark_cards is False     # no ark on the send side any more
    assert conn.supports_card_messages is True  # 图文卡片 msg_type=8
    assert conn.supports_poke is False
    assert conn.receives_all_messages is False  # not proven yet
    assert conn.needs_attention is False


def test_needs_attention_follows_reception_rather_than_a_constant():
    conn = _conn()
    conn._recv_msg_setting = "all"
    assert conn.receives_all_messages is True
    assert conn.needs_attention is True

    conn._recv_msg_setting = "only_mention"
    conn._all_messages_observed = True
    assert conn.receives_all_messages is True   # an actual push is proof by itself


def test_group_list_reports_observed_groups_instead_of_a_flat_empty():
    conn = _conn()
    assert run(conn.get_group_list()) == []
    conn._remember_group("C26B")
    conn._remember_group("AAAA")
    assert [g["group_id"] for g in run(conn.get_group_list())] == ["AAAA", "C26B"]


# ── receive loop routing ───────────────────────────────────────────────────

def _frame(op, *, t=None, d=None, s=7, event_id="evt-1"):
    payload = {"op": op, "s": s, "d": d or {}}
    if t is not None:
        payload["t"] = t
    if event_id:
        payload["id"] = event_id
    return json.dumps(payload)


class _Ws:
    def __init__(self, conn, frames):
        self.conn = conn
        self.frames = list(frames)
        self.sent = []

    async def recv(self):
        if not self.frames:
            raise ConnectionResetError("no more frames")
        return self.frames.pop(0)

    async def send(self, text):
        self.sent.append(text)

    async def close(self):
        return None


def _drive(conn, frames):
    ws = _Ws(conn, frames)
    conn._ws = ws
    conn.ws = ws

    async def reconnect():
        conn._closing = True

    conn._try_reconnect = reconnect
    run(conn._receive_loop())
    return ws


def test_full_group_messages_reach_the_queue_and_prove_the_mode():
    conn = _conn()
    _drive(conn, [
        _frame(0, t="GROUP_MESSAGE_CREATE", d={
            "id": "m1", "content": "大家早", "group_openid": "G1",
            "author": {"member_openid": "MA", "username": "小明"}}),
    ])
    msg = conn._message_queue.get_nowait()
    assert msg["message_type"] == "group" and msg["group_id"] == "G1"
    assert msg["user_nickname"] == "小明"
    assert msg["is_at_bot"] is False
    assert conn.receives_all_messages is True
    assert conn._seen_groups  # remembered for get_group_list


def test_at_message_marks_itself_as_addressed_to_the_bot():
    conn = _conn()
    _drive(conn, [
        _frame(0, t="GROUP_AT_MESSAGE_CREATE", d={
            "id": "m2", "content": "<@!10001> 在吗", "group_id": "G1",
            "author": {"member_openid": "MA"}}),
    ])
    msg = conn._message_queue.get_nowait()
    assert msg["is_at_bot"] is True
    assert "10001" in msg["mentioned_user_ids"]


def test_notice_events_go_to_sinks_and_not_into_the_message_queue():
    conn = _conn()
    seen: list[tuple[str, dict]] = []
    conn.add_event_sink(lambda et, payload: seen.append((et, payload)))
    _drive(conn, [
        _frame(0, t="GROUP_ADD_ROBOT", d={"group_openid": "G9", "op_member_openid": "OP"}),
        _frame(0, t="FRIEND_ADD", d={"user_openid": "U7"}),
    ])
    assert [et for et, _ in seen] == ["GROUP_ADD_ROBOT", "FRIEND_ADD"]
    assert seen[0][1]["group_id"] == "G9"
    assert seen[0][1]["event_id"] == "evt-1"   # usable as a passive-reply event_id
    assert conn._message_queue.empty()


def test_a_sink_exception_does_not_take_the_connection_down():
    conn = _conn()
    logs: list[str] = []
    conn.logger = type("L", (), {"warning": staticmethod(lambda m: logs.append(m)),
                                 "info": staticmethod(lambda m: None)})()

    def boom(event_type, payload):
        raise RuntimeError("consumer broke")

    conn.add_event_sink(boom)
    _drive(conn, [_frame(0, t="GROUP_DEL_ROBOT", d={"group_openid": "G9"})])
    assert any("事件监听器异常" in line for line in logs)


def test_unknown_event_names_are_logged_instead_of_vanishing(caplog):
    conn = _conn()
    messages: list[str] = []
    conn.logger = type("L", (), {"info": staticmethod(lambda m: messages.append(m)),
                                 "warning": staticmethod(lambda m: None)})()
    _drive(conn, [
        _frame(0, t="SOME_NEWER_EVENT", d={"x": 1}),
        _frame(0, t="SOME_NEWER_EVENT", d={"x": 2}),
    ])
    hits = [line for line in messages if "SOME_NEWER_EVENT" in line]
    assert len(hits) == 1  # 每种类型只记一次，不刷屏


# ── REST layer ─────────────────────────────────────────────────────────────

def test_recall_uses_the_right_endpoint_per_scope():
    conn = _conn()
    assert run(conn.recall_message("M1", group_id="G1")) is True
    assert conn._http.calls[-1][:2] == ("DELETE", "https://api.sgroup.qq.com/v2/groups/G1/messages/M1")
    run(conn.recall_message("M2", user_id="U1"))
    assert conn._http.calls[-1][1].endswith("/v2/users/U1/messages/M2")
    with pytest.raises(ValueError):
        run(conn.recall_message("M3"))   # no scope: guessing could hit the wrong chat


def test_api_error_carries_err_code_and_trace_id():
    conn = _conn([_Response({"err_code": 40034005, "message": "回复消息msg_id已过期",
                             "trace_id": "T42"}, 400)])
    with pytest.raises(QQOpenPlatformAPIError) as caught:
        run(conn.get_group_info("G1"))
    assert caught.value.err_code == 40034005
    assert "T42" in str(caught.value)


def test_expired_token_is_refreshed_and_the_call_retried_once():
    conn = _conn([
        _Response({"err_code": 40011027, "message": "AccessToken无效或过期"}, 401),
        _Response({"group_name": "读书会"}),
    ])
    refreshed: list[int] = []
    conn._refresh_token = lambda: _note(refreshed)

    async def _note(sink):
        sink.append(1)

    info = run(conn.get_group_info("G1"))
    assert info == {"group_name": "读书会"}
    assert len(conn._http.calls) == 2


def test_muted_rejection_is_recorded_so_the_caller_stops_sending():
    conn = _conn([_Response({"err_code": 850018, "message": "群被禁言或者机器人被禁言"}, 400)])
    with pytest.raises(QQOpenPlatformAPIError):
        run(conn.get_group_info("G5"))
    assert conn.is_group_muted("G5") is True
    assert conn.is_group_muted("G6") is False


def test_member_list_pages_until_the_cursor_runs_out():
    conn = _conn([
        _Response({"members": [{"member_openid": "A"}], "next_cursor": "c1"}),
        _Response({"members": [{"member_openid": "B"}], "next_cursor": ""}),
    ])
    assert [m["member_openid"] for m in run(conn.get_group_member_list("G1"))] == ["A", "B"]
    assert "cursor=c1" in conn._http.calls[1][1]


def test_mute_sends_an_expiry_timestamp_and_del_lifts_it():
    conn = _conn()
    run(conn.set_group_ban("G1", "M1", 600))
    body = conn._http.calls[-1][2]
    assert body["members"][0]["op"] == "add"
    assert body["members"][0]["mute_expire_at"].endswith("+08:00")
    run(conn.set_group_ban("G1", "M1", 0))
    assert conn._http.calls[-1][2]["members"][0]["op"] == "del"


# ── review follow-ups: intents opt-in, mute codes, 204, richer mute state ──

def test_intents_stay_opt_in_beyond_the_always_sent_bits():
    conn = QQOpenPlatformConnection(app_id="1", client_secret="2")
    assert conn._intents == (1 << 25) | (1 << 12)
    opted = QQOpenPlatformConnection(
        app_id="1", client_secret="2", extra_intents=1 << 24)
    assert opted._intents == (1 << 25) | (1 << 12) | (1 << 24)


def test_both_documented_mute_codes_are_recorded():
    for code in (850018, 40054002):
        conn = _conn([_Response({"err_code": code, "message": "机器人已被禁言"}, 400)])
        with pytest.raises(QQOpenPlatformAPIError):
            run(conn.get_group_info("G5"))
        assert conn.is_group_muted("G5") is True


def test_write_calls_may_succeed_as_204_with_no_body():
    conn = _conn([_Response(None, 204)])
    assert run(conn.set_group_ban("G1", "M1", 0)) == {"status": "ok"}
    assert conn._http.calls[-1][0] == "POST"


def test_bot_state_lands_the_member_openid():
    conn = _conn([_Response({"recv_msg_setting": "all", "member_role": "member",
                             "member_openid": "BOT1"})])
    run(conn.get_group_bot_state("G1"))
    assert conn._bot_member_openid == "BOT1"


def test_shut_list_keeps_the_global_rule():
    conn = _conn([_Response({"members": [{"member_openid": "A"}],
                             "global_rule": {"mode": "always"}})])
    assert [m["member_openid"] for m in run(conn.get_group_shut_list("G1"))] == ["A"]
    assert conn.group_shut_global_rule("G1") == {"mode": "always"}


def test_remuting_an_already_muted_member_uses_update():
    conn = _conn([_Response({"members": [{"member_openid": "M1"}]}), _Response({})])
    run(conn.set_group_ban("G1", "M1", 600))
    assert conn._http.calls[0][1].endswith("/v2/groups/G1/restrict_chat_setting")
    assert conn._http.calls[-1][2]["members"][0]["op"] == "update"


def test_declined_join_request_can_move_the_applier_to_the_blacklist():
    conn = _conn()
    run(conn.set_group_add_request("F1", approve=False, reason="不合适",
                                   group_id="G1", user_id="M1",
                                   add_to_member_blacklist=True))
    body = conn._http.calls[-1][2]
    assert body["reject_reason"] == "不合适"
    assert body["add_to_member_blacklist"] is True


def test_seen_groups_expire():
    conn = _conn()
    conn._remember_group("G1")
    conn._seen_groups["G1"] -= 31 * 24 * 3600
    conn._remember_group("G2")
    assert [g["group_id"] for g in run(conn.get_group_list())] == ["G2"]


def test_voice_message_uploads_file_type_three_then_sends_msg_type_seven():
    conn = _conn([_Response({"file_info": "FI-voice"}), _Response({"id": "m-voice"})])
    assert run(conn.send_group_record("G1", "https://cdn.example/a.wav")) == "m-voice"
    upload = conn._http.calls[0]
    assert upload[1].endswith("/v2/groups/G1/files")
    assert upload[2]["file_type"] == 3 and upload[2]["srv_send_msg"] is False
    assert conn._http.calls[1][2] == {"msg_type": 7, "media": {"file_info": "FI-voice"}}
    assert "m-voice" in conn.sent_message_ids


def test_ark_card_request_is_translated_into_a_card_payload():
    conn = _conn()
    assert run(conn.send_group_ark_card("G1", {"ark": {"kv": [
        {"key": "#TITLE#", "value": "状态"}, {"key": "#DESC#", "value": "一切正常"},
        {"key": "#URL#", "value": "http://a.example/x"}, {"key": "#IMG#", "value": "http://i/y"},
    ]}})) is True
    body = conn._http.calls[-1][2]
    assert body["msg_type"] == 8
    card = body["card"]
    assert card["type"] == "tuwen"
    assert card["content"]["title"] == "状态" and card["content"]["description"] == "一切正常"
    # https only: the platform drops non-https links rather than rendering them
    assert card["content"]["url"].startswith("https://")
    assert card["content"]["pic_url"].startswith("https://")


# ── unsupported operations ─────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "set_msg_emoji_like", "send_group_forward_msg", "get_group_msg_history",
    "mark_group_msg_as_read", "set_group_whole_ban", "get_cookies",
    "get_group_file_system_info", "delete_friend", "get_stranger_info",
])
def test_unsupported_operations_raise_with_a_reason(name):
    conn = _conn()
    with pytest.raises(QQOpenPlatformUnsupportedError) as caught:
        run(getattr(conn, name)(group_id="G1", user_id="U1", message_id="M1"))
    assert "v2" in str(caught.value) or "platform" in str(caught.value).lower()


def test_poke_is_unsupported_on_every_entry_point():
    conn = _conn()
    for name in ("send_group_poke", "group_poke", "friend_poke"):
        with pytest.raises(QQOpenPlatformUnsupportedError):
            run(getattr(conn, name)(group_id="G1", user_id="U1"))


def test_stubs_are_gone_from_the_surface():
    """No operation may still answer ``{}`` because it has nothing to say.

    A silent empty dict is what made these look successful: mute detection read
    ``shut_up_timestamp`` out of an empty dict and never fired, reactions looked sent.
    """
    conn = _conn()
    import inspect

    suspicious = []
    for name, member in inspect.getmembers(QQOpenPlatformConnection, inspect.iscoroutinefunction):
        if name.startswith("_"):
            # 内部辅助允许空响应（open_platform_media._media_post 有文档说明）；
            # 门禁只管对外操作面。
            continue
        source = inspect.getsource(member)
        if source.strip().endswith("return {}") or source.strip().endswith("return []"):
            suspicious.append(name)
    assert not suspicious, f"仍然只回空值的方法: {suspicious}"
