"""Plugin-supplied ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` placeholder substitution
contract (issue #1337).

Five host-side injection sites must funnel plugin-supplied text through the
same substitution helper:

1. ``_render_callback_inner_item`` — proactive/passive callback drain into
   the LLM prompt.
2. ``_format_voice_swap_item`` — voice-mode ``pending_extra_replies``
   hot-swap rendering into ``prime_context``.
3. ``app/main_server/character_runtime.py`` direct_reply — plugin text bypassing the LLM and
   going verbatim to TTS via ``send_lanlan_response``.
4. ``app/main_server/character_runtime.py`` ``passthrough_to_chat_bubble`` — ``visibility=["chat"]``
   + ``ai_behavior="blind"`` blind chat-bubble passthrough.
5. ``app/main_server/character_runtime.py`` HUD ``agent_notification`` — ``visibility=["hud"]``
   toast text.

If any of these grow a new code path that bypasses ``apply_role_placeholders``,
plugins emitting ``"向 {MASTER_NAME} 汇报…"`` style text will end up speaking /  # noqa: DOCSTRING_CJK
displaying the literal token to the user. This file is the canary.

The substitution uses ``str.replace``, not ``str.format`` — JSON fragments
or arbitrary ``{`` in user content must NOT raise ``KeyError``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# apply_role_placeholders — the single source of truth
# ---------------------------------------------------------------------------


def test_apply_role_placeholders_replaces_both_tokens():
    from main_logic.core import apply_role_placeholders

    out = apply_role_placeholders(
        "向 {MASTER_NAME} 汇报：{LANLAN_NAME} 已经完成任务",
        lanlan_name="兰兰",
        master_name="小明",
    )
    assert out == "向 小明 汇报：兰兰 已经完成任务"


def test_apply_role_placeholders_leaves_unknown_tokens_alone():
    from main_logic.core import apply_role_placeholders

    out = apply_role_placeholders(
        "{MASTER_NAME} 喜欢 {UNKNOWN_TOKEN}",
        lanlan_name="兰兰",
        master_name="小明",
    )
    assert out == "小明 喜欢 {UNKNOWN_TOKEN}"


def test_apply_role_placeholders_keeps_literal_braces_in_detail():
    """Plugin ``detail`` may carry JSON fragments / code snippets with bare
    ``{``. The helper must use ``str.replace``, not ``str.format`` — otherwise
    these crash with KeyError before the AI ever sees them."""
    from main_logic.core import apply_role_placeholders

    text = '收到工具返回：{"status": "ok", "msg": "done"} → 通知 {MASTER_NAME}'
    out = apply_role_placeholders(text, lanlan_name="兰兰", master_name="小明")
    assert '{"status": "ok"' in out
    assert "通知 小明" in out


def test_apply_role_placeholders_empty_name_leaves_token_literal():
    """When the host hasn't resolved a name yet (extremely early
    initialization), the helper must leave the placeholder as-is rather than
    replacing with the empty string and producing broken sentences like
    '向  汇报' or 'Hi, ! Welcome.'."""
    from main_logic.core import apply_role_placeholders

    out = apply_role_placeholders(
        "{MASTER_NAME} 在等 {LANLAN_NAME}",
        lanlan_name="",
        master_name="",
    )
    assert "{MASTER_NAME}" in out
    assert "{LANLAN_NAME}" in out


def test_apply_role_placeholders_empty_text_short_circuits():
    """Empty/None text is preserved as-is (so callers don't need to
    pre-check)."""
    from main_logic.core import apply_role_placeholders

    assert apply_role_placeholders("", lanlan_name="兰兰", master_name="小明") == ""


# ---------------------------------------------------------------------------
# _render_callback_inner_item — the LLM-prompt drain path
# ---------------------------------------------------------------------------


def test_render_callback_inner_item_substitutes_in_summary_and_detail():
    from main_logic.core import _render_callback_inner_item

    cb = {
        "summary": "向 {MASTER_NAME} 汇报",
        "detail": "{LANLAN_NAME} 完成了拾取任务",
        "status": "completed",
    }
    out = _render_callback_inner_item(
        cb, lang="zh", lanlan_name="兰兰", master_name="小明",
    )
    assert "向 小明 汇报" in out
    assert "兰兰 完成了拾取任务" in out
    assert "{MASTER_NAME}" not in out
    assert "{LANLAN_NAME}" not in out


# ---------------------------------------------------------------------------
# _format_voice_swap_item — the voice-mode hot-swap path
# ---------------------------------------------------------------------------


def test_voice_swap_item_substitutes_summary():
    from main_logic.core import _format_voice_swap_item

    entry = {
        "origin": "task_result",
        "summary": "刚才向 {MASTER_NAME} 演示了新功能",
        "detail": "",
        "status": "completed",
        "source_kind": "plugin",
        "source_name": "demo",
        "error_message": "",
    }
    out = _format_voice_swap_item(
        entry, "zh", lanlan_name="兰兰", master_name="小明",
    )
    assert "向 小明 演示了新功能" in out
    assert "{MASTER_NAME}" not in out


def test_voice_swap_item_substitutes_detail_when_summary_empty():
    from main_logic.core import _format_voice_swap_item

    entry = {
        "origin": "event",
        "summary": "",
        "detail": "{LANLAN_NAME} 收到了一条新弹幕",
        "status": "completed",
        "source_kind": "plugin",
        "source_name": "bilibili_danmaku",
        "error_message": "",
    }
    out = _format_voice_swap_item(
        entry, "zh", lanlan_name="兰兰", master_name="小明",
    )
    assert "兰兰 收到了一条新弹幕" in out


def test_voice_swap_render_pipeline_plumbs_names_through():
    """Integration-style check: the full hot-swap renderer reaches its inner
    item formatter with the names plumbed through. Pins the (caller →
    helper) contract that was broken pre-fix."""
    from main_logic.core import _render_pending_extra_replies_by_origin

    out = _render_pending_extra_replies_by_origin(
        [
            {
                "origin": "event",
                "summary": "向 {MASTER_NAME} 发送了一条问候",
                "detail": "",
                "status": "completed",
                "source_kind": "plugin",
                "source_name": "greeter",
                "error_message": "",
            }
        ],
        lang="zh",
        lanlan_name="兰兰",
        master_name="小明",
    )
    assert "向 小明 发送了一条问候" in out
    assert "{MASTER_NAME}" not in out


# ---------------------------------------------------------------------------
# main_server _handle_agent_event — verbatim plugin-text exits
# ---------------------------------------------------------------------------
#
# Three exits skip the LLM and render plugin text to the user directly. Each
# must funnel through ``core.apply_role_placeholders`` before reaching TTS /
# chat bubble / HUD; otherwise a plugin writing ``"通知 {MASTER_NAME}"`` would
# display the literal token. These tests fake the session manager and
# trigger _handle_agent_event end-to-end so the wiring stays covered.


def _mgr_for_main_server(send_lanlan_return=True):
    """Stub session manager with attributes / methods main_server reads on
    the verbatim-exit paths."""
    fake_mgr = MagicMock()
    fake_mgr.lanlan_name = "兰兰"
    fake_mgr.master_name = "小明"
    fake_mgr.send_lanlan_response = AsyncMock(return_value=send_lanlan_return)
    fake_mgr.handle_proactive_complete = AsyncMock()
    fake_mgr.passthrough_to_chat_bubble = AsyncMock(return_value=True)
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = MagicMock()
    fake_mgr.websocket.send_json = AsyncMock()
    fake_mgr._pending_agent_callback_task = None
    return fake_mgr


def _patch_main_server(monkeypatch, fake_mgr):
    monkeypatch.setattr("app.main_server.character_runtime._get_session_manager", lambda name: fake_mgr)
    monkeypatch.setattr("app.main_server.character_runtime._is_websocket_connected", lambda ws: True)


@pytest.mark.unit
async def test_main_server_direct_reply_substitutes_master_name(monkeypatch, capsys):
    """task_result + direct_reply → text goes verbatim to send_lanlan_response
    (skipping the LLM). The placeholder must be expanded at this boundary or
    the literal token reaches TTS."""
    from app import main_server

    fake_mgr = _mgr_for_main_server()
    _patch_main_server(monkeypatch, fake_mgr)
    private_reply = "PRIVATE_DIRECT_REPLY_MARKER"

    await main_server._handle_agent_event(
        {
            "event_type": "task_result",
            "lanlan_name": "兰兰",
            "text": "ignored",
            "detail": f"搞定了，{private_reply}，要不要跟 {{MASTER_NAME}} 报告一下？",
            "direct_reply": True,
            "channel": "plugin:demo",
            "task_id": "t-1",
            "media_parts": [],
        }
    )

    fake_mgr.send_lanlan_response.assert_awaited_once()
    sent_text = fake_mgr.send_lanlan_response.await_args.args[0]
    assert "{MASTER_NAME}" not in sent_text
    assert "跟 小明 报告" in sent_text
    captured = capsys.readouterr()
    assert private_reply not in captured.out
    assert private_reply not in captured.err


@pytest.mark.unit
async def test_main_server_chat_passthrough_substitutes_master_name(monkeypatch):
    """visibility=["chat"] + ai_behavior="blind" → text goes verbatim to
    ``passthrough_to_chat_bubble`` (skipping the LLM). Without substitution
    the literal placeholder renders in the chat bubble. This is the codex
    P2 finding on PR #1422."""
    from app import main_server

    fake_mgr = _mgr_for_main_server()
    _patch_main_server(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(
        {
            "event_type": "proactive_message",
            "lanlan_name": "兰兰",
            "text": "{MASTER_NAME} 你看一下这个",
            "channel": "plugin:demo",
            "task_id": "t-2",
            "ai_behavior": "blind",
            "visibility": ["chat"],
            "source_kind": "plugin",
            "source_name": "demo",
            "media_parts": [],
        }
    )

    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()
    sent_text = fake_mgr.passthrough_to_chat_bubble.await_args.args[0]
    assert "{MASTER_NAME}" not in sent_text
    assert "小明 你看一下这个" in sent_text


@pytest.mark.unit
async def test_main_server_hud_notification_substitutes_master_name(
    monkeypatch, capsys
):
    """visibility=["hud"] toast text reaches the frontend verbatim. Without
    substitution the placeholder shows up in the toast literal."""
    from app import main_server

    fake_mgr = _mgr_for_main_server()
    _patch_main_server(monkeypatch, fake_mgr)
    private_notification = "PRIVATE_HUD_NOTIFICATION_MARKER"

    await main_server._handle_agent_event(
        {
            "event_type": "proactive_message",
            "lanlan_name": "兰兰",
            "text": f"提醒 {{MASTER_NAME}}：{private_notification}，番茄钟到点了",
            "channel": "plugin:demo",
            "task_id": "t-3",
            "ai_behavior": "blind",
            "visibility": ["hud"],
            "source_kind": "plugin",
            "source_name": "demo",
            "media_parts": [],
        }
    )

    hud_calls = [
        c.args[0]
        for c in fake_mgr.websocket.send_json.await_args_list
        if c.args and isinstance(c.args[0], dict) and c.args[0].get("type") == "agent_notification"
    ]
    assert len(hud_calls) == 1
    notif_text = hud_calls[0].get("text", "")
    assert "{MASTER_NAME}" not in notif_text
    assert "提醒 小明：" in notif_text
    captured = capsys.readouterr()
    assert private_notification not in captured.out
    assert private_notification not in captured.err


@pytest.mark.unit
async def test_main_server_event_failure_logs_only_exception_type(monkeypatch):
    """Unexpected dispatch failures stay visible without persisting private text."""
    from app import main_server
    from app.main_server import character_runtime

    private_error = "PRIVATE_EXCEPTION_MESSAGE_MARKER"

    def fail_session_lookup(_lanlan_name):
        raise RuntimeError(private_error)

    test_logger = MagicMock()
    monkeypatch.setattr(character_runtime, "_get_session_manager", fail_session_lookup)
    monkeypatch.setattr(character_runtime, "logger", test_logger)

    await main_server._handle_agent_event(
        {
            "event_type": "task_update",
            "lanlan_name": "兰兰",
            "task": {},
        }
    )

    test_logger.warning.assert_called_once_with(
        "[EventBus] handle_agent_event failed (error_type=%s)",
        "RuntimeError",
    )
    rendered = (
        test_logger.warning.call_args.args[0] % test_logger.warning.call_args.args[1:]
    )
    assert private_error not in rendered
