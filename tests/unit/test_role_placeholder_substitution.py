"""Plugin-supplied ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` placeholder substitution
contract (issue #1337).

Three host-side injection sites must funnel plugin-supplied text through the
same substitution helper:

1. ``_render_callback_inner_item`` — proactive/passive callback drain into
   the LLM prompt.
2. ``_format_voice_swap_item`` — voice-mode ``pending_extra_replies``
   hot-swap rendering into ``prime_context``.
3. ``app/main_server.py`` direct_reply path — plugin text bypassing the LLM
   and going verbatim to TTS / chat bubble.

If any of these grow a new code path that bypasses ``apply_role_placeholders``,
plugins emitting ``"向 {MASTER_NAME} 汇报…"`` style text will end up speaking
the literal token to the user. This file is the canary.

The substitution uses ``str.replace``, not ``str.format`` — JSON fragments
or arbitrary ``{`` in user content must NOT raise ``KeyError``.
"""
from __future__ import annotations


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
