# -*- coding: utf-8 -*-
"""Unit tests for the analyzer-facing plugin payload view.

Covers the audience-boundary contract described in
``brain/plugin_analyzer_view.py``:

* Only ``running`` plugins reach the analyzer.
* ``__llm_tool__*``-prefixed entries are stripped.
* When a plugin's description literally references one of its
  ``@llm_tool`` names (the original ``minecraft_task`` leak), the
  sanitizer appends a stable boundary marker the analyzer prompt
  teaches the LLM to honor.
* The analyzer system prompt carries the rule in every supported
  language so the marker is interpretable regardless of UI locale.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# sanitize_plugins_for_analyzer
# ---------------------------------------------------------------------------


def _minecraft_like_plugin(status: str = "running") -> dict:
    """A payload mirroring the real game_agent_minecraft leak case.

    The description text literally names ``minecraft_task`` (the
    plugin author wrote it to guide the dialog LLM) AND the entries
    list contains an ``__llm_tool__minecraft_task`` registration plus
    real ``@plugin_entry`` queries the analyzer is supposed to see.
    """
    return {
        "id": "game_agent_minecraft",
        "status": status,
        "description": (
            "桥接本地 Minecraft Agent (WebSocket)，让 AI 通过 minecraft_task "
            "工具下达指令并实时观看游戏画面进行解说。"
        ),
        "entries": [
            {"id": "__llm_tool__minecraft_task", "description": "LLM-direct tool"},
            {"id": "game_agent_reload_config", "description": "reload"},
            {"id": "game_agent_status", "description": "status"},
            {"id": "query_inventory", "description": "inventory"},
        ],
    }


def test_drops_non_running_plugins():
    from brain.plugin_analyzer_view import sanitize_plugins_for_analyzer

    raw = [
        {"id": "running_one", "status": "running", "entries": [{"id": "a"}]},
        {"id": "stopped_one", "status": "stopped", "entries": [{"id": "a"}]},
        {"id": "pending_one", "status": "pending", "entries": [{"id": "a"}]},
        {"id": "no_status", "entries": [{"id": "a"}]},  # missing status
        "not-a-dict",
        None,
    ]
    out = sanitize_plugins_for_analyzer(raw)
    assert [p["id"] for p in out] == ["running_one"]


def test_strips_llm_tool_entries_from_visible_entries():
    from brain.plugin_analyzer_view import sanitize_plugins_for_analyzer

    out = sanitize_plugins_for_analyzer([_minecraft_like_plugin()])
    assert len(out) == 1
    entry_ids = [e["id"] for e in out[0]["entries"]]
    # All __llm_tool__ entries gone; @plugin_entry entries preserved
    # in original order.
    assert "__llm_tool__minecraft_task" not in entry_ids
    assert entry_ids == [
        "game_agent_reload_config",
        "game_agent_status",
        "query_inventory",
    ]


def test_appends_boundary_marker_when_llm_tools_were_stripped():
    from brain.plugin_analyzer_view import (
        LLM_DIRECT_BOUNDARY_MARKER,
        sanitize_plugins_for_analyzer,
    )

    out = sanitize_plugins_for_analyzer([_minecraft_like_plugin()])
    desc = out[0]["description"]
    # The original Chinese description text is preserved verbatim.
    assert "minecraft_task" in desc  # original text + marker list both contain it
    assert "桥接本地 Minecraft Agent" in desc
    # The marker appears AFTER the original description.
    marker_pos = desc.find(LLM_DIRECT_BOUNDARY_MARKER)
    text_pos = desc.find("桥接本地 Minecraft Agent")
    assert marker_pos > text_pos > -1
    # And it carries the bare tool name (no ``__llm_tool__`` prefix).
    assert f"{LLM_DIRECT_BOUNDARY_MARKER}: minecraft_task" in desc


def test_no_marker_when_plugin_has_no_llm_tools():
    """Plugins without ``@llm_tool`` exposure shouldn't pay the prompt
    tokens for a marker that has nothing to say."""
    from brain.plugin_analyzer_view import (
        LLM_DIRECT_BOUNDARY_MARKER,
        sanitize_plugins_for_analyzer,
    )

    raw = [{
        "id": "calendar",
        "status": "running",
        "description": "calendar plugin does meeting scheduling",
        "entries": [
            {"id": "list_events"},
            {"id": "create_event"},
        ],
    }]
    out = sanitize_plugins_for_analyzer(raw)
    assert LLM_DIRECT_BOUNDARY_MARKER not in out[0]["description"]
    assert out[0]["description"] == "calendar plugin does meeting scheduling"


def test_handles_missing_or_empty_description():
    from brain.plugin_analyzer_view import (
        LLM_DIRECT_BOUNDARY_MARKER,
        sanitize_plugins_for_analyzer,
    )

    raw = [
        {
            "id": "p1",
            "status": "running",
            # no description field at all
            "entries": [{"id": "__llm_tool__solo"}, {"id": "real"}],
        },
        {
            "id": "p2",
            "status": "running",
            "description": "",
            "entries": [{"id": "__llm_tool__solo"}, {"id": "real"}],
        },
    ]
    out = sanitize_plugins_for_analyzer(raw)
    for plugin in out:
        # When the original description is missing/empty, the marker
        # still lands so the analyzer learns about the boundary; the
        # marker is the entire description.
        assert plugin["description"].startswith(LLM_DIRECT_BOUNDARY_MARKER)
        assert plugin["description"].endswith(": solo")


def test_multiple_llm_tools_listed_in_order():
    from brain.plugin_analyzer_view import (
        LLM_DIRECT_BOUNDARY_MARKER,
        sanitize_plugins_for_analyzer,
    )

    raw = [{
        "id": "multi",
        "status": "running",
        "description": "multi-tool plugin",
        "entries": [
            {"id": "__llm_tool__alpha"},
            {"id": "real_entry"},
            {"id": "__llm_tool__beta"},
            {"id": "__llm_tool__gamma"},
        ],
    }]
    out = sanitize_plugins_for_analyzer(raw)
    expected_suffix = f"{LLM_DIRECT_BOUNDARY_MARKER}: alpha, beta, gamma"
    assert out[0]["description"].endswith(expected_suffix)
    assert [e["id"] for e in out[0]["entries"]] == ["real_entry"]


def test_returns_fresh_copies_input_untouched():
    """The sanitizer is called inside the HTTP provider — callers must
    not see the input mutated, otherwise repeated invocations would
    keep stacking marker suffixes on the same description string."""
    from brain.plugin_analyzer_view import sanitize_plugins_for_analyzer

    raw = [_minecraft_like_plugin()]
    raw_desc_before = raw[0]["description"]
    raw_entries_before = list(raw[0]["entries"])

    out = sanitize_plugins_for_analyzer(raw)

    # Input is intact: caller can still trust the original payload.
    assert raw[0]["description"] == raw_desc_before
    assert raw[0]["entries"] == raw_entries_before
    # And the output is a *different* dict object.
    assert out[0] is not raw[0]
    assert out[0]["entries"] is not raw[0]["entries"]


def test_idempotent_under_repeated_invocation():
    """Calling twice on the *output* of the first call shouldn't keep
    growing the description (defense-in-depth against double-stacking
    if the wiring ever re-routes the sanitized payload back through
    the helper)."""
    from brain.plugin_analyzer_view import (
        LLM_DIRECT_BOUNDARY_MARKER,
        sanitize_plugins_for_analyzer,
    )

    once = sanitize_plugins_for_analyzer([_minecraft_like_plugin()])
    twice = sanitize_plugins_for_analyzer(once)
    assert once[0]["description"] == twice[0]["description"]
    # And we still only see ONE marker (no double-appended boundary).
    assert twice[0]["description"].count(LLM_DIRECT_BOUNDARY_MARKER) == 1


def test_handles_non_list_entries_field_gracefully():
    from brain.plugin_analyzer_view import sanitize_plugins_for_analyzer

    raw = [{
        "id": "weird",
        "status": "running",
        "description": "broken entries shape",
        "entries": "not-a-list",
    }]
    out = sanitize_plugins_for_analyzer(raw)
    # Non-list entries pass through untouched; no marker because no
    # __llm_tool__ entries could be extracted.
    assert out[0]["entries"] == "not-a-list"
    assert out[0]["description"] == "broken entries shape"


def test_empty_and_none_input():
    from brain.plugin_analyzer_view import sanitize_plugins_for_analyzer

    assert sanitize_plugins_for_analyzer([]) == []
    assert sanitize_plugins_for_analyzer(None) == []


# ---------------------------------------------------------------------------
# Integration with _build_plugin_desc_lines (the actual analyzer prompt
# assembly path). This is the original regression case: the analyzer
# LLM must not be able to derive a callable ``entry_id=minecraft_task``
# from what reaches its prompt.
# ---------------------------------------------------------------------------


def test_build_plugin_desc_lines_never_yields_callable_llm_tool_entry():
    """End-to-end: the sanitized payload + the existing entry-list
    renderer together produce a prompt where ``minecraft_task`` is
    *only* present inside the boundary marker — never as a comma-
    separated callable entry like ``minecraft_task: ...``."""
    from brain.plugin_analyzer_view import (
        LLM_DIRECT_BOUNDARY_MARKER,
        sanitize_plugins_for_analyzer,
    )
    from brain.task_executor import DirectTaskExecutor

    sanitized = sanitize_plugins_for_analyzer([_minecraft_like_plugin()])
    executor = object.__new__(DirectTaskExecutor)
    lines = executor._build_plugin_desc_lines(sanitized)
    assert len(lines) == 1
    rendered = lines[0]

    # The entries: [...] block must NOT contain a callable
    # minecraft_task entry. _build_plugin_desc_lines formats each
    # entry as ``{id}: {desc}`` so the unambiguous tell-tale is
    # ``minecraft_task`` appearing at the *start* of a ``;``-separated
    # chunk inside the ``entries: [ ... ]`` segment.
    entries_segment = rendered.split("entries:", 1)[1]
    callable_chunks = [c.strip() for c in entries_segment.lstrip(" [").split(";")]
    assert not any(c.startswith("minecraft_task") for c in callable_chunks), (
        f"minecraft_task leaked as a callable entry: {callable_chunks}"
    )

    # The boundary marker (which lives in the description, before
    # ``| entries:``) tells the analyzer this name is off-limits.
    desc_segment = rendered.split("| entries:", 1)[0]
    assert LLM_DIRECT_BOUNDARY_MARKER in desc_segment
    assert "minecraft_task" in desc_segment


# ---------------------------------------------------------------------------
# Prompt template carries the rule in every supported locale
# ---------------------------------------------------------------------------


def test_user_plugin_prompt_contains_boundary_rule_in_every_locale():
    """If a locale forgets the rule, the analyzer LLM in that locale
    silently regresses to the old behavior. Pin every supported
    language."""
    from brain.plugin_analyzer_view import LLM_DIRECT_BOUNDARY_MARKER
    from config.prompts.prompts_agent import USER_PLUGIN_SYSTEM_PROMPT

    expected_langs = {"zh", "en", "ja", "ko", "ru", "es", "pt"}
    assert expected_langs.issubset(USER_PLUGIN_SYSTEM_PROMPT.keys())
    for lang in expected_langs:
        template = USER_PLUGIN_SYSTEM_PROMPT[lang]
        # The marker token itself must appear so the LLM has an
        # exact-string anchor to recognize.
        assert LLM_DIRECT_BOUNDARY_MARKER in template, (
            f"locale {lang!r} missing the LLM-direct boundary marker"
        )
