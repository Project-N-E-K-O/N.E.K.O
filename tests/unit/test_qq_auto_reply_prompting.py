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
