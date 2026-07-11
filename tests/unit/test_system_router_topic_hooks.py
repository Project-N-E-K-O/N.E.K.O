from pathlib import Path
from types import SimpleNamespace

import main_routers.system_router.proactive_chat_flow as system_router
from main_routers.system_router import (
    _open_threads_for_activity_state,
    _render_followup_topic_hooks,
    _resolve_topic_hook_locale,
)
from utils.llm_client import anthropic_retry_error_types


def test_proactive_llm_retry_errors_include_anthropic_transients():
    retry_types = system_router._proactive_llm_retry_error_types()
    anthropic_types = anthropic_retry_error_types()
    # anthropic 缺席时 accessor 兜底返回 ()，for 循环会零迭代假绿——CI/开发环境必装
    # anthropic，空即测试环境坏了，别让本测试静默空跑。
    assert anthropic_types, "anthropic retry error types 为空，本测试在空跑"
    for error_type in anthropic_types:
        assert error_type in retry_types


def test_screen_only_and_unfinished_thread_suppress_softer_open_threads():
    restricted = SimpleNamespace(propensity="restricted_screen_only", unfinished_thread=None)
    restricted_with_thread = SimpleNamespace(
        propensity="restricted_screen_only",
        unfinished_thread={"text": "刚才没聊完的问题"},
    )
    normal = SimpleNamespace(propensity="open", unfinished_thread=None)

    threads = ["AI 答应看测试还没看"]
    assert _open_threads_for_activity_state(None, threads) == threads
    assert _open_threads_for_activity_state(normal, threads) == threads
    assert _open_threads_for_activity_state(restricted, threads) == []
    assert _open_threads_for_activity_state(restricted_with_thread, threads) == []


def test_followup_surfaced_ids_are_limited_to_rendered_topics():
    topics = [
        {
            "id": f"reflection-{idx}",
            "text": f"follow-up memory {idx}",
        }
        for idx in range(4)
    ]

    prompt, surfaced_ids = _render_followup_topic_hooks("en", topics)

    assert "follow-up memory 0" in prompt
    assert "follow-up memory 1" in prompt
    assert "follow-up memory 2" in prompt
    assert "follow-up memory 3" not in prompt
    assert surfaced_ids == [
        "reflection-0",
        "reflection-1",
        "reflection-2",
    ]


def test_followup_surfaced_ids_skip_blank_and_duplicate_within_first_three():
    # A blank or duplicate followup inside the first three is dropped by the
    # prompt's dedup filter, so its id must NOT be reported as surfaced (else
    # /record_surfaced cools down a reflection the model never saw).
    topics = [
        {"id": "rendered-a", "text": "follow-up alpha"},
        {"id": "blank", "text": "   "},
        {"id": "dup", "text": "follow-up alpha"},
        {"id": "rendered-b", "text": "follow-up beta"},
    ]

    prompt, surfaced_ids = _render_followup_topic_hooks("en", topics)

    assert "follow-up alpha" in prompt
    # "beta" is the 4th topic and never reaches the rendered [:3] slice.
    assert "follow-up beta" not in prompt
    assert surfaced_ids == ["rendered-a"]


def test_topic_hook_locale_preserves_traditional_chinese_request_language():
    mgr = SimpleNamespace(user_language="zh-CN")

    topic_hook_lang = _resolve_topic_hook_locale(
        {"language": "zh-TW"},
        mgr,
        fallback="zh",
    )
    prompt, _surfaced_ids = _render_followup_topic_hooks(
        topic_hook_lang,
        [{"id": "reflection-tw", "text": "最近想用繁體中文聊城市流行"}],
    )

    assert topic_hook_lang == "zh-TW"
    assert "回憶線索：" in prompt
    assert "回忆线索：" not in prompt


def test_topic_hook_locale_falls_back_to_full_global_language(monkeypatch):
    mgr = SimpleNamespace(user_language=None)
    monkeypatch.setattr(system_router, "get_global_language_full", lambda: "zh-TW", raising=False)

    topic_hook_lang = _resolve_topic_hook_locale({}, mgr, fallback="zh")
    prompt, _surfaced_ids = _render_followup_topic_hooks(
        topic_hook_lang,
        [{"id": "reflection-global-tw", "text": "最近想用繁體中文聊城市流行"}],
    )

    assert topic_hook_lang == "zh-TW"
    assert "回憶線索：" in prompt
    assert "回忆线索：" not in prompt


def test_open_threads_compute_uses_topic_hook_locale():
    source = Path(system_router.__file__).read_text(encoding="utf-8")

    assert "topic_hook_lang = _resolve_topic_hook_locale(data, mgr, fallback=proactive_lang)" in source
    assert "kickoff_open_threads_compute(lang=topic_hook_lang)" in source
