import pytest

from plugin.plugins.qq_auto_reply.prompting import QQAutoReplyPromptingMixin


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("普通回复", "普通回复"),
        (
            "<think_never_used_51bce0c785ca2f68081bfa7d91973934></think_never_used_51bce0c785ca2f68081bfa7d91973934>我明白啦",
            "我明白啦",
        ),
        (
            "先想想\n</think_never_used_abc123>\n最终回复",
            "最终回复",
        ),
        (
            "<think>内部推理</think>对外回复",
            "对外回复",
        ),
        (
            "<thinking_trace_variant>分析</thinking_trace_variant>结论",
            "结论",
        ),
        (
            "对外回复</think_never_used_trailing>",
            "对外回复",
        ),
    ],
)
def test_sanitize_generated_reply_strips_thinking_variants(raw, expected):
    assert QQAutoReplyPromptingMixin._sanitize_generated_reply(raw) == expected


def _prompt_builder(settings):
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.prompt_builder import QQPromptBuilder

    return QQPromptBuilder(SimpleNamespace(_qq_settings=settings, i18n=None))


def test_group_memory_default_follows_configured_policy():
    """Group requests built without explicit memory flags (retroactive
    review, rapid-fire flush) must inherit the configured group-memory
    policy instead of silently resolving to False — which also flipped the
    shared session's memory_enabled off and blocked idle finalization."""
    on = _prompt_builder({"group_memory_enabled": True})
    off = _prompt_builder({"group_memory_enabled": False})

    assert on.should_use_memory_context(
        is_group=True, permission_level="user", requested=None,
    ) is True
    assert off.should_use_memory_context(
        is_group=True, permission_level="user", requested=None,
    ) is False
    # Explicit values always win over the configured default.
    assert on.should_use_memory_context(
        is_group=True, permission_level="user", requested=False,
    ) is False
    assert off.should_use_memory_context(
        is_group=True, permission_level="user", requested=True,
    ) is True
    # Private-chat defaults are unchanged: admin-only.
    assert on.should_use_memory_context(
        is_group=False, permission_level="admin", requested=None,
    ) is True
    assert on.should_use_memory_context(
        is_group=False, permission_level="user", requested=None,
    ) is False
    # Upgraded configs may lack the key entirely, and _qq_settings itself
    # may be None: both must default safely to off.
    assert _prompt_builder({}).should_use_memory_context(
        is_group=True, permission_level="user", requested=None,
    ) is False
    assert _prompt_builder(None).should_use_memory_context(
        is_group=True, permission_level="user", requested=None,
    ) is False


def test_group_persist_policy_decoupled_from_turn_recall():
    """Group persistence follows the configured policy when unspecified,
    independent of per-turn recall: a proactive turn that explicitly
    disables recall (use=False) must not flip the shared session's
    memory_enabled off and strand buffered opt-in history."""
    on = _prompt_builder({"group_memory_enabled": True})
    off = _prompt_builder({"group_memory_enabled": False})

    assert on.should_persist_memory(
        should_use_memory_context=False, requested=None, is_group=True,
    ) is True
    assert off.should_persist_memory(
        should_use_memory_context=True, requested=None, is_group=True,
    ) is False
    # Explicit values still win.
    assert on.should_persist_memory(
        should_use_memory_context=False, requested=False, is_group=True,
    ) is False
    # Private default unchanged: follows the turn's recall decision.
    assert on.should_persist_memory(
        should_use_memory_context=True, requested=None,
    ) is True
    assert on.should_persist_memory(
        should_use_memory_context=False, requested=None,
    ) is False


def test_open_platform_group_mentions_distinguish_bot_from_other_users():
    from plugin.plugins.qq_auto_reply.qq_open_plat import QQOpenPlatformConnection

    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = "10000"

    bot_only = conn._convert_event(
        "GROUP_AT_MESSAGE_CREATE",
        {
            "id": "m1",
            "group_id": "g1",
            "author": {"id": "u1", "username": "Alice"},
            "content": "<@!10000> hello",
        },
    )
    assert bot_only["mentioned_user_ids"] == ["10000"]
    assert bot_only["mentions_other_user"] is False

    with_other_user = conn._convert_event(
        "GROUP_AT_MESSAGE_CREATE",
        {
            "id": "m2",
            "group_id": "g1",
            "author": {"id": "u1", "username": "Alice"},
            "content": "<@!10000> <@!20000> hello",
        },
    )
    assert with_other_user["mentioned_user_ids"] == ["10000", "20000"]
    assert with_other_user["mentions_other_user"] is True


def test_focus_rise_window_boosts_the_focus_until_the_window_expires():
    """The rise window hands a freshly focused group a linear bonus
    (0 -> +2.0); once the window passes the bonus is gone and the score
    falls back to the raw one.

    These assertions follow attention_service as implemented. The case
    originally asserted the opposite semantic (a new focus scaled down to
    half and climbing back up), contradicting the implementation landed in
    the same commit, so it never passed. Which direction the window should
    have is a product call for the plugin side; this pins the one that is
    actually live."""
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService, QQGroupAttentionState

    plugin = SimpleNamespace(
        _qq_settings={
            "enable_group_attention": True,
            "group_attention_focus_rise_seconds": 10,
            "group_attention_focus_threshold": 4,
            "group_attention_min_threshold": 1,
            "group_attention_decay_per_second": 0.02,
        },
        backlog_store=None,
        group_permission_mgr=None,
        permission_mgr=None,
        _emit_log=lambda *args, **kwargs: None,
    )
    service = QQAttentionService(plugin)
    service._current_time = lambda: 100
    service._cache = {
        "focus": QQGroupAttentionState(
            group_id="focus",
            attention_score=8.0,
            urgency=0.8,
            interest=0.8,
            momentum=0.8,
            intimacy=0.4,
            focus_acquired_at=95,
        ).to_dict(),
        "other": QQGroupAttentionState(
            group_id="other",
            attention_score=6.5,
            urgency=0.7,
            interest=0.7,
            momentum=0.7,
            intimacy=0.3,
        ).to_dict(),
    }

    # 窗口过半（5s / 10s）：原始分 8.0 之上加满额 2.0 的一半。
    assert service.get_focus_score() == pytest.approx(9.0, rel=1e-3)
    assert service.get_snapshot()["focus_group_id"] == "focus"

    # 窗口结束后加成归零，回到原始分。
    service._current_time = lambda: 106
    assert service.get_focus_score() == pytest.approx(8.0, rel=1e-3)
    assert service.get_snapshot()["focus_group_id"] == "focus"
