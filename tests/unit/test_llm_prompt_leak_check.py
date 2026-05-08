"""Tests for utils.llm_prompt_leak_check.

Pre-condition for the assertion-mode tests in this file: pytest itself sets
``PYTEST_CURRENT_TEST`` while executing each test, which is exactly the
condition under which ``llm_prompt_leak_check`` switches from "log warning"
to "raise AssertionError". So just calling the detector with leaky input
inside a test function is enough to exercise the raise path.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from utils import llm_prompt_leak_check
from utils.llm_prompt_leak_check import (
    check_dict_strings_for_leaks,
    check_messages_for_leaks,
    check_text_for_leaks,
)


# ────────────────────────────────────────────────────────────────
# check_text_for_leaks: positive / negative cases
# ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("{master}", ["{master}"]),
        ("hello {master}, today is {weekday}", ["{master}", "{weekday}"]),
        ("foo {x} bar", ["{x}"]),
        ("{a1b2_c3}", ["{a1b2_c3}"]),
        # Doubled braces are an .format() escape → not a leak.
        ("{{escaped}}", []),
        ("Use {{master}} for the literal value.", []),
        # Empty / non-template braces should not match.
        ("", []),
        ("{}", []),
        ("{ master }", []),  # spaces → not a Python identifier in single braces
        # Pure punctuation noise.
        ("text with } and { but no pair", []),
        # Numeric leading char — also not the bug class we chase.
        ("{0}", []),
        # Non-string falls back to no leaks rather than raising.
        (None, []),
        (12345, []),
    ],
)
def test_check_text_for_leaks(text, expected):
    assert check_text_for_leaks(text) == expected


# ────────────────────────────────────────────────────────────────
# check_messages_for_leaks: only system role is scanned
# ────────────────────────────────────────────────────────────────


def test_user_role_with_braces_is_ignored():
    """User content can legitimately contain `{...}` (code snippets, JSON)."""
    messages = [
        {"role": "user", "content": "How do I render `{master}` in Jinja?"},
        {"role": "assistant", "content": "Use `{{ master }}` not `{master}`."},
    ]
    # Must not raise — these are non-system roles by design.
    check_messages_for_leaks(messages)


def test_system_role_with_leak_raises_in_pytest():
    messages = [{"role": "system", "content": "Hello {master}, focus on the task."}]
    with pytest.raises(AssertionError) as exc:
        check_messages_for_leaks(messages, context="unit-test")
    assert "{master}" in str(exc.value)
    assert "unit-test" in str(exc.value)


def test_system_role_without_leak_is_clean():
    messages = [{"role": "system", "content": "You are a helpful assistant."}]
    check_messages_for_leaks(messages)


def test_system_multimodal_content_is_scanned():
    """System messages with multimodal content (list of parts) must be scanned;
    image_url parts must be skipped (no false-positive on b64 payload)."""
    messages = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "Greet {master} warmly."},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abcd"},
                },
            ],
        }
    ]
    with pytest.raises(AssertionError) as exc:
        check_messages_for_leaks(messages)
    assert "{master}" in str(exc.value)


def test_doubled_braces_in_system_is_clean():
    messages = [
        {"role": "system", "content": "Use literal {{master}} for the example."},
    ]
    check_messages_for_leaks(messages)


def test_empty_messages_is_noop():
    check_messages_for_leaks([])
    check_messages_for_leaks(None)


def test_non_dict_entries_are_skipped():
    """Defensive: caller passes a list with stray non-dicts → skip silently."""
    check_messages_for_leaks(["raw string", 42, None])


def test_assertion_can_be_disabled_outside_pytest_context(monkeypatch):
    """In production (no PYTEST_CURRENT_TEST and no NEKO_PROMPT_LEAK_RAISE),
    leaks log a warning rather than raise."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("NEKO_PROMPT_LEAK_RAISE", raising=False)
    messages = [{"role": "system", "content": "Hi {master}"}]
    with patch.object(llm_prompt_leak_check.logger, "warning") as warn:
        check_messages_for_leaks(messages, context="prod-sim")
    warn.assert_called_once()
    (warn_msg,), _ = warn.call_args
    assert "{master}" in warn_msg
    assert "prod-sim" in warn_msg


def test_force_raise_via_env(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("NEKO_PROMPT_LEAK_RAISE", "1")
    messages = [{"role": "system", "content": "Hi {master}"}]
    with pytest.raises(AssertionError):
        check_messages_for_leaks(messages)


# ────────────────────────────────────────────────────────────────
# check_dict_strings_for_leaks: realtime path
# ────────────────────────────────────────────────────────────────


def test_dict_strings_nested_leak():
    """Mirrors the realtime session config: instruction nested several
    levels deep. Detector must surface the path."""
    config = {
        "instructions": "Hello {master}, please help.",
        "voice": "alloy",
    }
    with pytest.raises(AssertionError) as exc:
        check_dict_strings_for_leaks(config, context="realtime")
    assert "{master}" in str(exc.value)
    assert "instructions" in str(exc.value)


def test_dict_strings_deeply_nested_leak():
    """Gemini-Live-shaped config: setup.system_instruction.parts[0].text"""
    config = {
        "setup": {
            "system_instruction": {
                "parts": [{"text": "Be helpful to {master}."}],
            },
        },
    }
    with pytest.raises(AssertionError) as exc:
        check_dict_strings_for_leaks(config, context="gemini-live")
    assert "{master}" in str(exc.value)


def test_dict_strings_no_leak():
    config = {
        "instructions": "Be helpful.",
        "voice": "alloy",
        "tools": [{"name": "lookup"}],
    }
    check_dict_strings_for_leaks(config)


def test_dict_strings_doubled_brace_not_a_leak():
    config = {"instructions": "Use {{master}} as a placeholder."}
    check_dict_strings_for_leaks(config)


# ────────────────────────────────────────────────────────────────
# Integration: ChatOpenAI._params actually calls the detector
# ────────────────────────────────────────────────────────────────


def _build_chat_openai():
    """Build a ChatOpenAI without hitting the network. Constructor only
    instantiates the SDK clients; that's fine in a unit test because we
    never call the SDK methods."""
    from utils.llm_client import ChatOpenAI

    return ChatOpenAI(
        model="test-model",
        base_url="http://127.0.0.1:0/v1",
        api_key="test",
    )


def test_chat_openai_params_raises_on_system_leak():
    client = _build_chat_openai()
    messages = [{"role": "system", "content": "Hello {master}."}]
    with pytest.raises(AssertionError):
        client._params(messages)


def test_chat_openai_params_clean_passes():
    client = _build_chat_openai()
    messages = [{"role": "system", "content": "Be helpful."}]
    p = client._params(messages)
    assert p["messages"][0]["content"] == "Be helpful."


def test_chat_openai_params_user_braces_pass():
    """Regression guard: user content with literal `{x}` must NOT raise."""
    client = _build_chat_openai()
    messages = [
        {"role": "system", "content": "Be helpful."},
        {"role": "user", "content": "What does `{name}` mean in Python f-strings?"},
    ]
    client._params(messages)


# ────────────────────────────────────────────────────────────────
# PR #1075 regression smoke
# ────────────────────────────────────────────────────────────────


def test_pr1075_regression_smoke():
    """Mimic the exact failure mode from PR #1075:

    A prompt-dict entry contains ``{master}`` (added so the footer mirrors
    the header's placeholder). The consumer fetches it via a ``_loc``-style
    helper that returns the raw template, then concatenates straight into
    the system message — no ``.format()`` between the two. The detector
    must catch this end-to-end via the ``_params`` chokepoint.
    """
    SCREEN_SECTION_FOOTER = {
        "zh": "（屏幕信息结束，{master}）",
        "en": "(End of screen info, {master})",
    }

    def _loc(d: dict, lang: str) -> str:  # mimics config.prompts_sys._loc
        return d.get(lang, d.get("en", d.get("zh", "")))

    sf = _loc(SCREEN_SECTION_FOOTER, "en")  # bug: forgot .format(master=name)
    system_prompt = f"You are a helpful assistant.\n\n{sf}"

    client = _build_chat_openai()
    with pytest.raises(AssertionError) as exc:
        client._params([{"role": "system", "content": system_prompt}])
    assert "{master}" in str(exc.value)


def test_pr1075_regression_fixed_path():
    """Same scenario, but the consumer remembered to .format(). Must pass."""
    SCREEN_SECTION_FOOTER = {
        "en": "(End of screen info, {master})",
    }
    sf = SCREEN_SECTION_FOOTER["en"].format(master="Alice")
    system_prompt = f"You are a helpful assistant.\n\n{sf}"

    client = _build_chat_openai()
    p = client._params([{"role": "system", "content": system_prompt}])
    assert "{master}" not in p["messages"][0]["content"]
    assert "Alice" in p["messages"][0]["content"]
