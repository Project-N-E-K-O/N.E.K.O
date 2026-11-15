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
import base64
import json

import pytest

from utils.connection.qq import open_platform as mod
from utils.connection.qq import open_platform as open_plat_mod
from utils.connection.qq.open_platform import (
    QQOpenPlatformAPIError,
    QQOpenPlatformConnection,
    QQOpenPlatformUnsupportedError,
)


class _Log:
    """Collects the warnings the connector emits."""

    def __init__(self):
        self.lines: list[str] = []

    def warning(self, message: str) -> None:
        self.lines.append(message)


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


def test_needs_attention_follows_proved_reception_not_a_setting():
    # #3706's rule, kept through the per-group rework: only an actually delivered
    # GROUP_MESSAGE_CREATE is evidence. A setting, a probe or a subscription is not,
    # because the intents bitmap is ours to submit while the privilege is a human's to
    # grant -- per group.
    conn = _conn()
    conn._recv_msg_setting_by_group["G1"] = "all"
    assert conn.receives_all_messages is False
    assert conn.group_receives_all_messages("G1") is False
    assert conn.group_recv_msg_setting("G1") == "all"    # still reported, never believed

    conn._all_messages_observed_groups.add("G1")
    assert conn.receives_all_messages is True
    assert conn.needs_attention is True
    assert conn.group_receives_all_messages("G1") is True
    # A second group that pushed nothing must not inherit the first one's proof.
    assert conn.group_receives_all_messages("G2") is False


def test_one_group_push_change_does_not_answer_for_every_group():
    # The per-group facts used to live in connection-wide fields, so the last group
    # asked decided the role and the setting for all of them.
    conn = _conn([
        _Response({"recv_msg_setting": "all", "member_role": "admin", "member_openid": "B1"}),
        _Response({"recv_msg_setting": "only_mention", "member_role": "member", "member_openid": "B2"}),
    ])
    run(conn.get_group_bot_state("G1"))
    run(conn.get_group_bot_state("G2"))
    assert conn.group_recv_msg_setting("G1") == "all"
    assert conn.group_recv_msg_setting("G2") == "only_mention"
    assert conn.group_bot_member_role("G1") == "admin"
    assert conn.group_bot_member_role("G2") == "member"
    assert conn._bot_openid_by_group == {"G1": "B1", "G2": "B2"}
    # Both connection-wide properties refuse to answer once two groups disagree:
    # picking one entry would make the result depend on which group spoke first.
    assert conn.bot_member_role == ""
    assert conn.recv_msg_setting == ""


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


def test_the_send_bodies_share_the_mute_and_token_handling():
    """Posting around `_api` hides `err_code`: no mute record, no refresh, no retry."""
    text = [{"type": "text", "data": {"text": "在的"}}]
    muted = _conn([_Response({"err_code": 40054002, "message": "机器人已被禁言"}, 400)])
    assert run(muted.send_group_message_segments("G5", text)) is None
    assert muted.is_group_muted("G5") is True

    card = _conn([_Response({"err_code": 40054002, "message": "机器人已被禁言"}, 400)])
    assert run(card.send_group_ark_card("G6", {"title": "状态"})) is False
    assert card.is_group_muted("G6") is True

    stale = _conn([_Response({"err_code": 40011027, "message": "AccessToken无效或过期"}, 401),
                   _Response({"access_token": "t2", "expires_in": 7200}),
                   _Response({"id": "m-new"})])
    assert run(stale.send_private_message_segments("U1", text)) == "m-new"
    assert stale._http.calls[1][1] == stale._TOKEN_URL
    assert len(stale._http.calls) == 3


def test_write_calls_may_succeed_as_204_with_no_body():
    conn = _conn([_Response(None, 204)])
    assert run(conn.set_group_ban("G1", "M1", 0)) == {"status": "ok"}
    assert conn._http.calls[-1][0] == "POST"


def test_bot_state_lands_the_member_openid():
    conn = _conn([_Response({"recv_msg_setting": "all", "member_role": "member",
                             "member_openid": "BOT1"})])
    run(conn.get_group_bot_state("G1"))
    # The id is keyed by group: the platform issues a different one per conversation.
    assert conn._bot_openid_by_group == {"G1": "BOT1"}
    assert conn._remembered_bot_openid("G1") == "BOT1"
    assert conn._remembered_bot_openid("G2") == ""


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


def test_ban_still_mutes_when_the_shut_list_query_fails():
    # A failed pre-read (missing rights, flaky network) must not turn into
    # "cannot mute anyone": fall back to ``add``.
    conn = _conn([_Response({"err_code": 40011028, "message": "no perms"}, 403),
                  _Response({})])
    assert run(conn.set_group_ban("G1", "M1", 600)) == {"status": "ok"}
    assert conn._http.calls[-1][2]["members"][0]["op"] == "add"


def test_ban_skips_malformed_entries_in_the_shut_list():
    conn = _conn([_Response({"members": ["junk", {"member_openid": "M1"}]}),
                  _Response({})])
    run(conn.set_group_ban("G1", "M1", 600))
    assert conn._http.calls[-1][2]["members"][0]["op"] == "update"


def test_get_group_list_filters_expired_ids_without_a_new_record():
    conn = _conn()
    conn._remember_group("G1")
    conn._seen_groups["G1"] -= 31 * 24 * 3600
    assert run(conn.get_group_list()) == []
    assert "G1" not in conn._seen_groups


def test_voice_message_uploads_file_type_three_then_sends_msg_type_seven():
    conn = _conn([_Response({"file_info": "FI-voice"}), _Response({"id": "m-voice"})])
    assert run(conn.send_group_record("G1", "https://cdn.example/a.wav")) == "m-voice"
    upload = conn._http.calls[0]
    assert upload[1].endswith("/v2/groups/G1/files")
    assert upload[2]["file_type"] == 3 and upload[2]["srv_send_msg"] is False
    assert conn._http.calls[1][2] == {"msg_type": 7, "media": {"file_info": "FI-voice"}}
    assert "m-voice" in conn.sent_message_ids


def test_local_media_source_reads_a_three_slash_file_uri(tmp_path):
    voice = tmp_path / "a b.wav"
    voice.write_bytes(b"RIFFfake")
    conn = _conn([_Response({"file_info": "FI-local"}), _Response({"id": "m-local"})])
    assert run(conn.send_private_record("U1", voice.as_uri())) == "m-local"
    upload = conn._http.calls[0]
    assert upload[1].endswith("/v2/users/U1/files")
    assert upload[2]["file_type"] == 3 and upload[2]["file_name"] == "a b.wav"
    assert base64.b64decode(upload[2]["file_data"]) == b"RIFFfake"


def test_base64_media_source_uploads_inline_bytes():
    conn = _conn([_Response({"file_info": "FI-b64"}), _Response({"id": "m-b64"})])
    source = "base64://" + base64.b64encode(b"RIFFfake").decode()
    assert run(conn.send_group_record("G1", source)) == "m-b64"
    body = conn._http.calls[0][2]
    assert body["file_type"] == 3
    assert base64.b64decode(body["file_data"]) == b"RIFFfake"


def test_a_media_source_that_cannot_be_read_is_named_in_the_log():
    conn = _conn()
    conn.logger = _Log()
    assert run(conn.send_group_record("G1", "no-such-voice.wav")) is None
    assert not conn._http.calls
    assert conn.logger.lines and "no-such-voice.wav" in conn.logger.lines[0]


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
    # https only: the platform drops non-https links rather than rendering them.
    # Whole values, not prefixes: asserting startswith("https://") is what let
    # "https://http://a.example/x" through this test for a whole review round.
    assert card["content"]["url"] == "https://a.example/x"
    assert card["content"]["pic_url"] == "https://i/y"


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


def test_poke_raises_on_the_non_contract_entry_points():
    conn = _conn()
    for name in ("group_poke", "friend_poke"):
        with pytest.raises(QQOpenPlatformUnsupportedError):
            run(getattr(conn, name)(group_id="G1", user_id="U1"))


def test_send_group_poke_answers_false_rather_than_raising():
    """The contract entry point returns a bool, so callers do not guard it.

    A poke is decoration: raising aborted the whole reply, and a text line that fakes a
    poke is worse than saying it was not sent.
    """
    conn = _conn()
    assert run(conn.send_group_poke("G1", "U1")) is False
    assert conn._http.calls == []


def test_stubs_are_gone_from_the_surface():
    """No public operation may answer a bare empty literal, anywhere in its body.

    A silent empty dict is what made these look successful: mute detection read
    ``shut_up_timestamp`` out of an empty dict and never fired, reactions looked sent.

    The first version of this gate looked only at the *last* line of each function, so
    it passed while thirteen methods still ``return {}`` on their missing-argument
    guard -- a return that is not last is the common case. It now walks the syntax tree,
    so any empty literal anywhere in any public coroutine trips it.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(mod))
    flagged: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if node.name.startswith("_"):
            # Internal helpers may answer empty (open_platform_media._media_post is
            # documented); the gate guards the outward operation surface.
            continue
        for ret in ast.walk(node):
            if not isinstance(ret, ast.Return) or ret.value is None:
                continue
            if isinstance(ret.value, (ast.Dict, ast.List)) and not ret.value.keys                     and not ret.value.elts:
                flagged.append(f"{node.name}:{ret.lineno}")
    assert not flagged, f"仍然静默回空值的方法: {flagged}"


def test_whole_group_mute_expires_when_nobody_lifts_it_loudly():
    # Greptile r4574476395: the platform sends no event when an admin lifts a
    # whole-group mute, so a cached rule without an expiry silenced the group for
    # the life of the process.
    conn = _conn()
    conn._shut_global_rule = {"G1": {"mode": "always"}}
    conn._shut_global_rule_at = {"G1": __import__("time").time()}
    assert conn.is_group_muted("G1") is True

    conn._shut_global_rule_at = {"G1": __import__("time").time() - 301}
    assert conn.is_group_muted("G1") is False
    assert "G1" not in conn._shut_global_rule


def test_shut_list_read_stamps_the_rule_age():
    conn = _conn([_Response({"members": [], "global_rule": {"mode": "always"}})])
    run(conn.get_group_shut_list("G1"))
    assert conn.group_shut_global_rule("G1") == {"mode": "always"}
    assert conn._shut_global_rule_at["G1"] > 0


def test_being_removed_from_a_group_forgets_its_mute_rule():
    conn = _conn()

    async def go():
        await conn._dispatch_event("GROUP_DEL_ROBOT", {"group_openid": "G1"}, "e1")

    run(go())
    assert "G1" not in conn._shut_global_rule
    assert "G1" not in conn._shut_global_rule_at


def test_oversized_base64_is_refused_before_it_is_decoded(monkeypatch):
    # Greptile r4574476408: the decode allocated the whole payload just to be
    # rejected by the size cap afterwards.
    from utils.connection.qq import open_platform as op

    calls = []
    monkeypatch.setattr(op, "UPLOAD_HARD_MAX_BYTES", 1024)

    def boom(*a, **kw):
        calls.append(1)
        raise AssertionError("decoded an oversized payload")

    monkeypatch.setattr(op, "_decode_base64_source", boom)
    conn = _conn()
    huge = "A" * (2048 * 2)                      # ~1.5 KB decoded, cap is 1 KB
    assert run(conn._upload_media(scope="groups", owner_id="G1",
                                  source="base64://" + huge, file_type=3)) == ""
    assert calls == []


def test_chunked_upload_failure_degrades_instead_of_raising(monkeypatch):
    # Greptile r4574476380: the chunked protocol posts through the media mixin,
    # which raises httpx errors instead of the classified one, and it never
    # checked the token. Either escaping here breaks the text fallback the
    # caller relies on.
    import httpx

    async def explode(**kwargs):
        raise httpx.ConnectError("upload service down")

    conn = _conn()
    monkeypatch.setattr(conn, "_media_upload_chunked", explode)
    monkeypatch.setattr("utils.connection.qq.open_platform.INLINE_UPLOAD_MAX_BYTES", 4)
    raw = b"x" * 64
    assert run(conn._upload_media(scope="groups", owner_id="G1",
                                  source="base64://" + __import__("base64").b64encode(raw).decode(),
                                  file_type=3)) == ""


def test_chunked_upload_checks_the_token_first(monkeypatch):
    seen = []

    async def fake_chunked(**kwargs):
        seen.append("chunked")
        return "FI"

    async def explode():
        raise QQOpenPlatformAPIError(method="POST", path="/token",
                                     message="token unavailable")

    conn = _conn()
    raw = "base64://" + __import__("base64").b64encode(b"y" * 64).decode()
    monkeypatch.setattr("utils.connection.qq.open_platform.INLINE_UPLOAD_MAX_BYTES", 4)
    monkeypatch.setattr(conn, "_media_upload_chunked", fake_chunked)

    async def dead_token():
        raise QQOpenPlatformAPIError(method="POST", path="/token",
                                     message="token unavailable")

    monkeypatch.setattr(conn, "_ensure_token", dead_token)
    assert run(conn._upload_media(scope="groups", owner_id="G1",
                                  source=raw, file_type=3)) == ""
    assert seen == []                            # a broken token never reaches upload

    async def live_token():
        return None

    monkeypatch.setattr(conn, "_ensure_token", live_token)
    assert run(conn._upload_media(scope="groups", owner_id="G1",
                                  source=raw, file_type=3)) == "FI"
    assert seen == ["chunked"]

def test_missing_target_ids_are_refused_out_loud():
    # The guard used to be a bare ``return {}``: the caller saw a success-shaped value
    # and nothing in the log said why the operation did nothing.
    conn = _conn()
    logs: list[str] = []
    conn.logger = type("L", (), {"warning": staticmethod(lambda m: logs.append(str(m))),
                                 "info": staticmethod(lambda m: None)})()
    assert run(conn.set_group_ban("", "M1", 600)) == {}
    assert run(conn.get_group_member_info("G1", "")) == {}
    assert run(conn.get_group_member_list("")) == []
    assert run(conn.respond_interaction("")) == {}
    assert len(logs) == 4
    assert all("缺少参数" in line for line in logs), logs


# ── round-3 follow-ups: card links, mute truth, token truth, size routing ──

def test_card_links_replace_the_scheme_instead_of_stacking_it():
    # The old normaliser prefixed https:// onto whatever it got, so http:// became
    # "https://http://..." -- a card that renders and links nowhere.
    conn = _conn()
    card = conn._card_message_payload({
        "content": {"title": "t", "url": "http://a.example/x", "pic_url": "http://cdn/y.png"}})
    assert card["content"]["url"] == "https://a.example/x"
    assert card["content"]["pic_url"] == "https://cdn/y.png"


def test_card_link_normalisation_covers_the_other_shapes():
    conn = _conn()
    def norm(url):
        return conn._card_message_payload({"content": {"title": "t", "url": url}})["content"]["url"]
    assert norm("https://a.example/x") == "https://a.example/x"   # already good
    assert norm("//a.example/x") == "https://a.example/x"          # protocol-relative
    assert norm("a.example/x") == "https://a.example/x"            # bare host
    assert norm("") == ""                                          # nothing to fix
    # A javascript: or data: link is refused by the platform anyway; the card loses the
    # link rather than pretending the link works.
    assert norm("javascript:alert(1)") == ""
    assert norm("data:text/html,x") == ""


def test_whole_group_mute_is_reported_even_without_a_per_member_record():
    # The PR stores global_rule and the reviewer's point was that nothing read it.
    # The rule is taken from a real shut-list read, which is also what stamps its age.
    conn = _conn([_Response({"members": [], "global_rule": {"mode": "always"}})])
    assert conn.is_group_muted("G1") is False
    run(conn.get_group_shut_list("G1"))
    assert conn.is_group_muted("G1") is True
    assert conn.is_group_muted("G2") is False


def test_token_response_without_a_token_is_an_error_not_an_empty_credential():
    # Accepting a parseable body as success used to blank the token and push the
    # expiry out, so every call for hours carried an empty Authorization header.
    conn = _conn([_Response({"err_code": 11252, "message": "appid or secret invalid"})])
    conn._token_expires_at = 0
    with pytest.raises(QQOpenPlatformAPIError):
        run(conn._refresh_token())
    assert conn._access_token == "t"          # the old value is still there
    assert conn._token_expires_at == 0        # and nothing promised a fresh window


def test_gateway_url_failure_is_a_classified_error():
    # The last REST call on the lifecycle path that bypassed the error layer.
    conn = _conn([_Response({"err_code": 40030011, "message": "no scope",
                             "trace_id": "T-77"}, 403)])
    with pytest.raises(QQOpenPlatformAPIError) as got:
        run(conn._get_gateway_url())
    assert got.value.trace_id == "T-77"
    assert got.value.err_code == 40030011


def test_oversized_local_voice_goes_through_the_chunked_protocol(tmp_path, monkeypatch):
    # One minute of 44.1 kHz stereo WAV is about 10 MB, over the 5 MB inline cap.
    # Refusing it was the reason voice looked dead on any real recording.
    seen = {}

    async def fake_chunked(*, scope, owner_id, payload, file_name, file_type, digests=None):
        seen.update(scope=scope, owner_id=owner_id, size=len(payload),
                    file_name=file_name, file_type=file_type, digests=digests)
        return "FI-big"

    conn = _conn()
    monkeypatch.setattr(conn, "_media_upload_chunked", fake_chunked)
    monkeypatch.setattr("utils.connection.qq.open_platform.INLINE_UPLOAD_MAX_BYTES", 4)
    voice = tmp_path / "long.wav"
    voice.write_bytes(b"RIFF" + b"0" * 40)
    assert run(conn._upload_media(scope="groups", owner_id="G1",
                                  source=str(voice), file_type=3)) == "FI-big"
    assert {k: v for k, v in seen.items() if k != "digests"} == {
        "scope": "groups", "owner_id": "G1", "size": 44,
        "file_name": "long.wav", "file_type": 3}
    assert seen["digests"] is not None            # hashed off the loop, then handed in


def test_huge_local_file_is_capped_before_it_is_held_in_memory(tmp_path, monkeypatch):
    # The cap used to be checked after read(), so an oversized path put the whole file
    # in memory only to be refused.
    calls = []

    def capped_read(path, limit=0):
        calls.append(limit)
        return b"x" * (limit + 1)          # pretend the file is bigger than the cap

    monkeypatch.setattr("utils.connection.qq.open_platform._read_bytes", capped_read)
    conn = _conn()
    assert run(conn._upload_media(scope="groups", owner_id="G1",
                                  source="C:/big.wav", file_type=3)) == ""
    assert calls and calls[0] > 0          # the read was told the ceiling


def test_quoting_the_bots_own_message_is_recognised_on_receive():
    conn = _conn()
    conn.record_sent_message_id("SENT-1")
    msg = conn._convert_event("GROUP_MESSAGE_CREATE", {
        "id": "m-ref", "group_openid": "G1", "content": "确实好用",
        "author": {"member_openid": "MA"},
        "message_reference": {"message_id": "SENT-1"}})
    assert msg["quoted_message_id"] == "SENT-1"
    assert msg["is_reply_to_bot"] is True
    other = conn._convert_event("GROUP_MESSAGE_CREATE", {
        "id": "m-ref2", "group_openid": "G1", "content": "hi",
        "author": {"member_openid": "MA"},
        "message_reference": {"message_id": "SOMEBODY-ELSE"}})
    assert other["is_reply_to_bot"] is False

def test_token_refresh_failure_on_the_retry_path_stays_classified():
    # The first token call in _api is wrapped; the one behind the 40011027 retry was
    # not, so a transport failure there escaped as a raw httpx error and broke the
    # documented "media send answers None" degrade.
    import httpx

    conn = _conn([
        _Response({"err_code": 40011027, "message": "token expired"}),
        httpx.ConnectError("token endpoint unreachable"),
    ])
    with pytest.raises(QQOpenPlatformAPIError) as got:
        run(conn._api("GET", "/v2/groups/G1/bot_state"))
    assert "token refresh failed" in str(got.value)


def test_explicit_file_name_survives_to_the_upload_body():
    # upload_group_file(file, name) took a name and threw it away: the body carried
    # the base64 magic-table guess (image.png) or the temporary basename, and that is
    # the name the recipient sees on the file.
    import base64 as _b64

    payload = "base64://" + _b64.b64encode(b"%PDF-1.4 fake").decode()

    conn = _conn([_Response({"file_info": "FI"}), _Response({"id": "m-file"})])
    run(conn.upload_group_file(payload, "report.pdf", group_id="G1"))
    assert conn._http.calls[0][2]["file_name"] == "report.pdf"

    conn2 = _conn([_Response({"file_info": "FI2"}), _Response({"id": "m-file2"})])
    run(conn2.upload_private_file(payload, "notes.txt", user_id="U1"))
    assert conn2._http.calls[0][2]["file_name"] == "notes.txt"

# ---- round 7: what the self-check found still open -------------------------

def test_at_frame_never_learns_the_bot_from_a_remaining_mention():
    # The v2 event page defines mentions as the users @-ed *excluding the bot itself*,
    # and content with the bot's own @ prefix already removed. So the target left in an
    # AT frame is a human. Caching it as this bot's openid made the bot answer that
    # human's conversations, read real summons as is_at_bot=False, and -- because a
    # learned openid also disarms the attribution hold and the one-shot diagnostic --
    # do all of it without a log line.
    conn = _conn()
    msg = conn._convert_event("GROUP_AT_MESSAGE_CREATE", {
        "id": "m-at", "group_openid": "G3", "content": "<@AAAAAAAAAAAAAAAA> 看这个",
        "author": {"member_openid": "H"},
    })
    assert msg["is_at_bot"] is True                    # the event type says so
    assert msg["mentions_other_user"] is True          # and the target is somebody else
    assert conn._remembered_bot_openid("G3") == ""     # nothing guessed

    later = conn._convert_event("GROUP_MESSAGE_CREATE", {
        "id": "m-later", "group_openid": "G3", "content": "还找 <@AAAAAAAAAAAAAAAA>",
        "author": {"member_openid": "H"},
    })
    assert later["is_at_bot"] is False
    assert later["mentions_other_user"] is True


def test_at_frame_with_is_you_false_does_not_learn_that_item():
    conn = _conn()
    conn._convert_event("GROUP_AT_MESSAGE_CREATE", {
        "id": "m-no", "group_openid": "G5", "content": "hi",
        "author": {"member_openid": "H"},
        "mentions": [{"member_openid": "BBBBBBBBBBBBBBBB", "is_you": False}],
    })
    assert conn._remembered_bot_openid("G5") == ""


def test_is_you_true_is_still_the_learning_path():
    conn = _conn()
    conn._convert_event("GROUP_MESSAGE_CREATE", {
        "id": "m-yes", "group_openid": "G6", "content": "hi",
        "author": {"member_openid": "H"},
        "mentions": [{"member_openid": "CCCCCCCCCCCCCCCC", "is_you": True}],
    })
    assert conn._remembered_bot_openid("G6") == "CCCCCCCCCCCCCCCC"


def test_proactive_permission_comes_from_bot_state_when_it_answers():
    # An absent key stays unknown. A present key overwrites what an older notice
    # wrote, or group_proactive_allowed() reports a previous session.
    conn = _conn([_Response({"recv_msg_setting": "only_mention", "member_role": "member",
                             "member_openid": "BOT1", "allow_proactive_msg": False})])
    conn._proactive_allowed_by_group["G1"] = True      # an earlier RECEIVE notice
    run(conn.get_group_bot_state("G1"))
    assert conn.group_proactive_allowed("G1") is False

    conn2 = _conn([_Response({"recv_msg_setting": "all", "member_role": "member"})])
    run(conn2.get_group_bot_state("G1"))
    assert conn2.group_proactive_allowed("G1") is None  # not answered, not defaulted


def test_card_send_records_its_id_so_a_quote_is_recognised():
    # Cards were the only send path that never registered its id, and the method
    # returns a bool so no caller could either. A user quoting a bot card read as
    # chatter and lost the queue priority addressed messages get.
    conn = _conn([_Response({"id": "card-1"})])
    ok = run(conn.send_group_ark_card("G1", {"ark": {"kv": [
        {"key": "#TITLE#", "value": "状态"}, {"key": "#DESC#", "value": "ok"},
        {"key": "#URL#", "value": "http://a.example/x"},
        {"key": "#IMG#", "value": "http://i/y"}]}}))
    assert ok is True
    assert "card-1" in conn.sent_message_ids

    quoted = conn._convert_event("GROUP_MESSAGE_CREATE", {
        "id": "m-quote", "group_openid": "G1", "content": "这个状态还在吗",
        "author": {"member_openid": "H"},
        "message_reference": {"message_id": "card-1"},
    })
    assert quoted["is_reply_to_bot"] is True
    assert quoted["quoted_message_id"] == "card-1"


def test_hold_skips_a_message_the_frame_already_decided():
    # A legacy <@!uin> payload whose id matches _self_id is already attributed.
    # Holding it would add latency and reorder the group for no information.
    import asyncio

    conn = _conn()
    conn._self_id = "7788"

    async def go():
        await conn._dispatch_event("GROUP_MESSAGE_CREATE", {
            "id": "m-decided", "group_openid": "G7", "content": "<@!7788> 在吗",
            "author": {"member_openid": "H"},
        }, "e1")
        await asyncio.sleep(0)
        return conn._message_queue.qsize(), dict(conn._attribution_hold)

    loop = asyncio.new_event_loop()
    try:
        size, holds = loop.run_until_complete(go())
    finally:
        loop.close()
    assert holds == {}
    assert size == 1


def test_at_frame_cancels_the_waiting_full_copy():
    import asyncio

    conn = _conn()
    full = {
        "id": "m-pair", "group_openid": "G8", "content": "几点开会 <@DDDDDDDDDDDDDDDD>",
        "author": {"member_openid": "H"},
    }
    at = dict(full, content="几点开会")

    async def go():
        await conn._dispatch_event("GROUP_MESSAGE_CREATE", full, "e1")
        await asyncio.sleep(0)
        held = dict(conn._attribution_hold)
        await conn._dispatch_event("GROUP_AT_MESSAGE_CREATE", at, "e2")
        await asyncio.sleep(0)
        return held, dict(conn._attribution_hold), conn._message_queue.qsize()

    loop = asyncio.new_event_loop()
    try:
        held_before, held_after, queued = loop.run_until_complete(go())
    finally:
        loop.close()
    assert "m-pair" in held_before            # the full copy waited
    assert held_after == {}                   # the AT frame took ownership
    assert queued == 1                        # exactly one delivery
    msg = conn._message_queue.get_nowait()
    assert msg["is_at_bot"] is True


def test_hold_saturates_instead_of_piling_up(monkeypatch):
    import asyncio

    monkeypatch.setattr("utils.connection.qq.open_platform._ATTRIBUTION_HOLD_MAX", 2)
    conn = _conn()

    async def go():
        for i in range(5):
            await conn._dispatch_event("GROUP_MESSAGE_CREATE", {
                "id": "m-sat-" + str(i), "group_openid": "G9",
                "content": "喂 <@EEEEEEEEEEEEEEEE" + str(i) + ">",
                "author": {"member_openid": "H"},
            }, "e")
            await asyncio.sleep(0)
        return len(conn._attribution_hold), conn._message_queue.qsize()

    loop = asyncio.new_event_loop()
    try:
        held, queued = loop.run_until_complete(go())
    finally:
        loop.close()
    assert held == 2                          # capped
    assert queued == 3                        # the rest went straight in


def test_identify_clears_the_probed_setting_as_well_as_the_observed_proof():
    import asyncio
    import json as _json

    conn = _conn()
    conn._recv_msg_setting_by_group["G1"] = "all"
    conn._proactive_allowed_by_group["G1"] = True
    conn._all_messages_observed_groups.add("G1")
    conn._access_token = "t"
    conn._token_expires_at = float("inf")

    class _WS:
        async def send(self, raw):
            return None

        async def recv(self):
            await asyncio.sleep(0)
            return _json.dumps({"op": 0, "t": "READY", "s": 1,
                                "d": {"session_id": "S", "user": {"id": "u"}}})

        async def close(self):
            return None

    conn._ws = _WS()
    run(conn._handshake(is_reconnect=False))
    assert conn.receives_all_messages is False
    assert conn.group_recv_msg_setting("G1") == ""
    assert conn.group_proactive_allowed("G1") is None


def test_token_failure_before_a_text_send_degrades_to_none():
    # The senders call the token pre-flight themselves, outside _api. When
    # _refresh_token started rejecting token-less bodies, that raised straight
    # through send_group_message_segments -- and the caller awaits it without a try.
    conn = _conn([_Response({"err_code": 1129, "message": "too many requests"})])
    conn._access_token = ""
    conn._token_expires_at = 0
    assert run(conn.send_group_message_segments("G1", [
        {"type": "text", "data": {"text": "hi"}}])) is None
    assert run(conn.send_private_message_segments("U1", [
        {"type": "text", "data": {"text": "hi"}}])) is None
    # Only the token attempt was made: no message POST burned a request.
    assert all("/messages" not in call[1] for call in conn._http.calls)


def test_chunked_upload_is_handed_precomputed_digests(monkeypatch):
    from utils.connection.qq import open_platform as op

    seen = {}

    async def fake_chunked(*, scope, owner_id, payload, file_name, file_type, digests=None):
        seen["digests"] = digests
        return "FI"

    conn = _conn()
    monkeypatch.setattr(conn, "_media_upload_chunked", fake_chunked)
    monkeypatch.setattr(op, "INLINE_UPLOAD_MAX_BYTES", 4)
    raw = b"z" * 64
    out = run(conn._upload_media(scope="groups", owner_id="G1", file_type=3,
                                 source="base64://" + __import__("base64").b64encode(raw).decode()))
    assert out == "FI"
    assert seen["digests"] == op._digests(raw)      # hashed off the loop, not inside


def test_connection_wide_flags_answer_when_every_group_agrees():
    conn = _conn()
    conn._recv_msg_setting_by_group.update({"G1": "all", "G2": "all"})
    assert conn.recv_msg_setting == "all"           # agreement is an answer
    conn._recv_msg_setting_by_group["G3"] = "only_mention"
    assert conn.recv_msg_setting == ""              # disagreement is not

# ── round 10: base64 measure, card reply anchors, blacklist batching ───────

def test_base64_at_the_exact_limit_is_not_refused_by_the_measure(monkeypatch):
    # greptile r4574585087. len()*3//4 counts the "=" padding as payload, so any size
    # whose encoding pads measures one byte over the ceiling and is refused: 209715200
    # bytes encodes to 279620268 characters with one pad, and the rough form calls that
    # 209715201. A trailing newline does the same to smaller payloads.
    import base64 as _b64

    monkeypatch.setattr(mod, "UPLOAD_HARD_MAX_BYTES", 2)
    padded = _b64.b64encode(b"ab").decode()              # "YWI=" -> 2 bytes, one pad
    conn = _conn([_Response({"file_info": "FI"}), _Response({"id": "m"})])
    assert run(conn._upload_media(scope="groups", owner_id="G1",
                                  source="base64://" + padded, file_type=3)) == "FI"

    wrapped = padded + chr(10)
    conn2 = _conn([_Response({"file_info": "FI2"}), _Response({"id": "m2"})])
    assert run(conn2._upload_media(scope="groups", owner_id="G1",
                                   source="base64://" + wrapped, file_type=3)) == "FI2"

    # One byte over the ceiling is still refused, and refused before decoding.
    conn3 = _conn([_Response({"file_info": "FI3"})])
    assert run(conn3._upload_media(scope="groups", owner_id="G1",
                                   source="base64://" + _b64.b64encode(b"abc").decode(),
                                   file_type=3)) == ""
    assert conn3._http.calls == []


def test_card_send_keeps_the_passive_reply_anchor():
    # Codex r4576712126: the translation replaced the whole body, so msg_id/msg_seq
    # carried by the caller were dropped and the card left as a proactive send --
    # refused in groups that disable proactive messages, and charged against the
    # active quota where it is allowed.
    conn = _conn([_Response({"id": "card-2"})])
    assert run(conn.send_group_ark_card(
        "G1", {"ark": {"kv": [{"key": "#TITLE#", "value": "t"}]}},
        reply_message_id="M-1")) is True
    body = conn._http.calls[-1][2]
    assert body["msg_type"] == 8
    assert body["msg_id"] == "M-1"
    assert body["msg_seq"] == 1
    assert "event_id" not in body

    conn2 = _conn([_Response({"id": "card-3"})])
    run(conn2.send_group_ark_card("G1", {
        "ark": {"kv": [{"key": "#TITLE#", "value": "t"}]},
        "msg_id": "M-2", "msg_seq": 3}))
    assert conn2._http.calls[-1][2]["msg_id"] == "M-2"
    assert conn2._http.calls[-1][2]["msg_seq"] == 3


def test_card_send_keeps_the_event_anchor():
    conn = _conn([_Response({"id": "card-4"})])
    run(conn.send_group_ark_card(
        "G1", {"ark": {"kv": [{"key": "#TITLE#", "value": "t"}]}}, event_id="E-9"))
    body = conn._http.calls[-1][2]
    assert body["event_id"] == "E-9"
    assert "msg_seq" not in body


def test_blacklist_batches_instead_of_dropping_the_tail():
    # greptile r4574519956: ``[:20]`` answered a 21-member call as success while the
    # platform had only ever seen 20, so the extra member stayed off the list and
    # nothing upstream retried.
    ids = ["M%02d" % i for i in range(21)]
    conn = _conn([_Response({"status": "ok"}), _Response({"status": "ok2"})])
    out = run(conn.operate_group_member_blacklist("G1", ids, op="add"))
    posts = [c for c in conn._http.calls if c[0] == "POST"]
    assert len(posts) == 2
    assert len(posts[0][2]["member_openids"]) == 20
    assert posts[1][2]["member_openids"] == ["M20"]
    # The path is the point: batching rewrote it to /blacklist, which the platform has
    # no such endpoint, so every add and remove failed while the test counted posts.
    assert all(call[1].endswith("/v2/groups/G1/member_blacklist") for call in posts), posts
    assert out["processed"] == 21
    assert out["batches"] == 2

    single = _conn([_Response({"status": "ok"})])
    out2 = run(single.operate_group_member_blacklist("G1", ids[:20], op="del"))
    assert len([c for c in single._http.calls if c[0] == "POST"]) == 1
    assert "processed" not in out2

# ── round 11: shared msg_seq, member paging, hold re-attribution ───────────

def test_explicit_card_seq_is_recorded_in_the_shared_counter():
    # greptile r4576805062 / CodeRabbit r4576821304: a card sent with msg_seq=1 left
    # the allocator untouched, so the next reply to the same message also got 1 and
    # the platform dropped it as a duplicate (msg_id, msg_seq).
    conn = _conn([_Response({"id": "c1"}), _Response({"id": "t1"})])
    assert run(conn.send_group_ark_card("G1", {
        "ark": {"kv": [{"key": "#TITLE#", "value": "t"}]},
        "msg_id": "M", "msg_seq": 1})) is True
    assert conn._msg_seq_by_reply_id["M"][0] == 1

    assert run(conn.send_group_message_segments("G1", [
        {"type": "reply", "data": {"id": "M"}},
        {"type": "text", "data": {"text": "后续"}}])) == "t1"
    assert conn._http.calls[-1][2]["msg_seq"] == 2


def test_card_seq_from_ark_is_not_used_for_a_keyword_anchor():
    # The seq belongs to the message the caller was replying to. Borrowing it for a
    # different anchor would repeat a pair the platform already saw.
    conn = _conn([_Response({"id": "c2"})])
    run(conn.send_group_ark_card(
        "G1",
        {"ark": {"kv": [{"key": "#TITLE#", "value": "t"}]},
         "msg_id": "OTHER", "msg_seq": 4},
        reply_message_id="M-NEW"))
    body = conn._http.calls[-1][2]
    assert body["msg_id"] == "M-NEW"
    assert body["msg_seq"] == 1


def test_member_list_pages_to_the_end_by_default():
    # greptile r4576752652: the old default of 10 pages dropped every member past
    # page 10 and still returned a plain list, so a caller could not tell the
    # difference between a small group and a truncated read.
    responses = []
    for page in range(14):
        responses.append(_Response({
            "members": [{"member_openid": f"M{page}_{i}"} for i in range(30)],
            "next_cursor": f"c{page + 1}" if page < 13 else "",
        }))
    conn = _conn(responses)
    out = run(conn.get_group_member_list("G1"))
    assert len(out) == 14 * 30
    assert conn._http.calls[-1][1].endswith("/members?cursor=c13")


def test_member_list_reports_when_a_cap_leaves_members_unread():
    conn = _conn()
    logs = []
    conn.logger = type("L", (), {
        "warning": staticmethod(lambda m: logs.append(str(m))),
        "info": staticmethod(lambda m: None)})()
    responses = [
        _Response({"members": [{"member_openid": f"A{i}"} for i in range(30)],
                   "next_cursor": f"c{i + 1}"})
        for i in range(5)
    ]
    conn._http.script = responses
    out = run(conn.get_group_member_list("G1", max_pages=2))
    assert len(out) == 60
    assert any("返回结果不完整" in line for line in logs), logs
    assert conn._member_pages_state["G1"]["next_cursor"] == "c2"


def test_member_list_stops_at_the_safety_cap_when_the_cursor_is_stuck():
    # greptile r4577011460, CodeRabbit r4577024272, wehos r4577285950. The bound used to
    # be `len(out) // 30 >= cap`. A page with no members grows neither term of that
    # expression, so an endpoint that keeps handing back the same cursor paged until the
    # request failed or something outside the loop cancelled it. The stuck-cursor case is
    # exactly the case the safety cap exists for, and it did not fire.
    conn = _conn([
        _Response({"members": [], "next_cursor": "same"})
        for _ in range(mod._MEMBER_PAGES_SAFETY_CAP + 5)
    ])
    out = run(conn.get_group_member_list("G1"))
    assert out == []
    # Scripted five extra pages so an over-run is visible as a count mismatch instead of
    # as a silently shorter list.
    assert len(conn._http.calls) == mod._MEMBER_PAGES_SAFETY_CAP
    assert conn._member_pages_state["G1"]["next_cursor"] == "same"


def test_member_list_counts_short_pages_as_requests_not_members():
    # Same root, other direction: `len(out) // 30 >= 2` is a member count, so a cap of
    # two pages kept running until 60 members arrived. wehos r4577285950 measured six
    # requests for max_pages=2 at ten members a page. A page cap is a promise about
    # requests, because the request is the thing that costs.
    conn = _conn([
        _Response({"members": [{"member_openid": f"A{i}"}], "next_cursor": f"c{i + 1}"})
        for i in range(6)
    ])
    out = run(conn.get_group_member_list("G1", max_pages=2))
    assert len(out) == 2
    assert len(conn._http.calls) == 2
    assert conn._member_pages_state["G1"]["next_cursor"] == "c2"


def test_held_copy_is_re_judged_after_the_identity_is_learned():
    # CodeRabbit r4576780342: M1 waits unattributed. M2 then proves the group's bot
    # openid through is_you. Releasing M1 with its stored verdict would keep calling a
    # real summon chatter, even though the identity is now known.
    import asyncio

    BOT = "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ1"
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = ""
    conn.logger = None
    conn._message_queue = asyncio.Queue(maxsize=8)
    m1 = {"id": "M1", "group_openid": "G1", "content": f"开会 <@{BOT}>",
          "author": {"member_openid": "H1"}}

    async def go():
        await conn._dispatch_event("GROUP_MESSAGE_CREATE", m1, "e1")
        await asyncio.sleep(0)
        assert conn._message_queue.empty()          # M1 is held, not queued
        m2 = {"id": "M2", "group_openid": "G1", "content": "早安",
              "author": {"member_openid": "H2"},
              "mentions": [{"member_openid": BOT, "is_you": True}]}
        await conn._dispatch_event("GROUP_MESSAGE_CREATE", m2, "e2")
        await asyncio.sleep(0)
        assert conn._remembered_bot_openid("G1") == BOT
        await asyncio.sleep(open_plat_mod._AT_MERGE_WINDOW_SECONDS + 0.05)
        delivered = []
        while not conn._message_queue.empty():
            delivered.append(conn._message_queue.get_nowait())
        return delivered

    delivered = asyncio.run(go())
    by_id = {d["message_id"]: d for d in delivered}
    assert by_id["M1"]["is_at_bot"] is True          # re-judged with the new identity
    assert by_id["M1"]["mentions_other_user"] is False

def test_base64_padding_across_a_newline_is_measured_like_the_decoder():
    # CodeRabbit r4576821307. ``_decode_base64_source`` drops ASCII whitespace before
    # decoding, so padding separated by a newline belongs to the same quartet. The old
    # measure subtracted only the contiguous run of "=" at the very end: it estimated
    # two bytes for a one-byte payload, and the pre-decode size check refused content
    # that was in fact inside the limit.
    import base64

    from utils.connection.qq.open_platform_media import _base64_decoded_size

    for raw in (b"a", b"ab", b"abc", b"abcd", b"abcde", b"abcdef"):
        encoded = base64.b64encode(raw).decode()
        assert _base64_decoded_size(encoded) == len(raw), encoded
        # Same payload, MIME-wrapped: the measure must not grow with the line breaks.
        wrapped = encoded[:2] + chr(10) + encoded[2:]
        assert _base64_decoded_size(wrapped) == len(raw), wrapped

    assert _base64_decoded_size("YQ=" + chr(10) + "=") == 1

    import utils.connection.qq.open_platform as op

    conn = _conn([_Response({"file_info": "FIx"}), _Response({"id": "mx"})])
    original = op.UPLOAD_HARD_MAX_BYTES
    op.UPLOAD_HARD_MAX_BYTES = 1
    try:
        assert run(conn._upload_media(
            scope="groups", owner_id="G1",
            source="base64://YQ=" + chr(10) + "=", file_type=3)) == "FIx"
    finally:
        op.UPLOAD_HARD_MAX_BYTES = original
