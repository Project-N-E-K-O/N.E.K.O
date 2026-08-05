from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import main_logic.public_knowledge_tool as knowledge_tool
from config.prompts._locale import NEKO_CORE_LOCALES
from config.prompts.prompts_knowledge import knowledge_prompt_language


class _Registry:
    def __init__(self) -> None:
        self.tool = None

    def register(self, tool, *, replace: bool) -> None:
        assert replace is True
        self.tool = tool


def test_public_knowledge_tool_schema_and_all_prompt_locales() -> None:
    for locale in NEKO_CORE_LOCALES:
        registry = _Registry()
        knowledge_tool.register_public_knowledge_tool(registry, language=locale)
        assert registry.tool.name == "query_public_knowledge"
        assert registry.tool.description
        assert registry.tool.parameters["properties"]["collection"]["enum"] == [
            "all",
            "meme",
            "corpora",
        ]
        expected = "zh-TW" if locale == "zh-TW" else "zh" if locale == "zh-CN" else locale
        assert knowledge_prompt_language(locale) == expected


@pytest.mark.asyncio
async def test_automatic_context_uses_one_card_and_records_no_content(monkeypatch) -> None:
    captured = {}

    class _Service:
        def build_conversation_context(self, text, *, limit):
            assert text == "visible user text"
            assert limit == 1
            return SimpleNamespace(
                text="temporary card",
                hit_count=1,
                match_mode="strong",
                collection_id="meme",
                entry_title="private fixture title",
                source_tag="source:fixture",
            )

    monkeypatch.setattr(
        knowledge_tool,
        "get_config_manager",
        lambda: SimpleNamespace(knowledge_dir="unused"),
    )
    monkeypatch.setattr(
        knowledge_tool,
        "open_builtin_knowledge",
        lambda _root: _Service(),
    )
    monkeypatch.setattr(
        knowledge_tool,
        "record_knowledge_route",
        lambda **values: captured.update(values),
    )

    result = await knowledge_tool.build_public_knowledge_turn_context(
        "visible user text"
    )

    assert result == "temporary card"
    assert captured == {
        "collection_id": "meme",
        "match_mode": "strong",
        "card_delivered": True,
        "result": "matched",
    }


@pytest.mark.asyncio
async def test_automatic_context_timeout_does_not_block_reply(monkeypatch) -> None:
    captured = {}

    def _slow_context(_text):
        time.sleep(0.35)
        raise AssertionError("late worker result must be ignored")

    monkeypatch.setattr(knowledge_tool, "_build_turn_context", _slow_context)
    monkeypatch.setattr(
        knowledge_tool,
        "PUBLIC_KNOWLEDGE_AUTO_CONTEXT_TIMEOUT_SECONDS",
        0.02,
    )
    monkeypatch.setattr(
        knowledge_tool,
        "record_knowledge_route",
        lambda **values: captured.update(values),
    )

    started = time.perf_counter()
    result = await knowledge_tool.build_public_knowledge_turn_context("hello")

    assert result == ""
    assert time.perf_counter() - started < 0.2
    assert captured == {"result": "timeout", "error_type": "TimeoutError"}


@pytest.mark.asyncio
async def test_tool_is_local_and_bounds_results(monkeypatch) -> None:
    seen = {}
    entries = [
        SimpleNamespace(
            title=f"Entry {index}",
            summary="summary",
            content="Meaning\n- used in context",
            source_tag="source:chime",
            tags=("source:chime", "type:quotation", "quality:stale-usage"),
        )
        for index in range(5)
    ]

    class _Service:
        def search(self, collection_id, query, *, limit):
            seen.setdefault("calls", []).append((collection_id, query, limit))
            return [
                SimpleNamespace(score=5 - index, entry=entry)
                for index, entry in enumerate(entries[:limit])
            ]

    monkeypatch.setattr(
        knowledge_tool,
        "get_config_manager",
        lambda: SimpleNamespace(knowledge_dir="unused"),
    )
    monkeypatch.setattr(
        knowledge_tool,
        "open_builtin_knowledge",
        lambda _root: _Service(),
    )

    result = await knowledge_tool.handle_public_knowledge_call(
        {"query": "fixture", "collection": "all", "limit": 99},
        language="en",
    )

    assert seen["calls"] == [
        ("meme", "fixture", 3),
        ("corpora", "fixture", 3),
    ]
    assert result.count("\n- [") == 3
    assert "memory" in result
    assert "Meme type: quotation" in result
    assert "Typical usage: used in context" in result
    assert "Source: CHIME | license: MIT" in result
    assert "usage may be outdated" in result
