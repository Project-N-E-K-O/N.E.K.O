"""Unit tests for the mcp-builder evaluation agent loop (`F0574`)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_PATH = REPO_ROOT / ".agent/skills/mcp-builder/scripts/evaluation.py"


def _load_evaluation_module(monkeypatch: pytest.MonkeyPatch):
    """Load `.agent/skills/mcp-builder/scripts/evaluation.py` with a stub `connections` module."""
    stub_connections = ModuleType("connections")
    stub_connections.create_connection = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "connections", stub_connections)

    spec = importlib.util.spec_from_file_location("mcp_builder_evaluation_under_test", EVALUATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tool_use_block(block_id: str, name: str, tool_input: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


@pytest.mark.asyncio
async def test_agent_loop_handles_parallel_tool_use_blocks(monkeypatch: pytest.MonkeyPatch):
    """Execute every `tool_use` block in a turn and send all `tool_result`s in one user message."""
    evaluation = _load_evaluation_module(monkeypatch)

    responses = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                _text_block("Calling two tools in parallel."),
                _tool_use_block("toolu_01", "search_docs", {"q": "alpha"}),
                _tool_use_block("toolu_02", "fetch_item", {"id": 42}),
            ],
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[_text_block("<response>done</response>")],
        ),
    ]
    recorded_messages: list[list[dict]] = []

    class _FakeMessages:
        def create(self, **kwargs):
            # Snapshot messages at each API call because `agent_loop` mutates the list in place.
            recorded_messages.append(json.loads(json.dumps(kwargs["messages"], default=str)))
            return responses.pop(0)

    fake_client = SimpleNamespace(messages=_FakeMessages())
    tool_calls: list[tuple[str, dict]] = []

    class _FakeConnection:
        async def call_tool(self, name: str, tool_input: dict):
            tool_calls.append((name, tool_input))
            if name == "search_docs":
                return {"hits": ["a"]}
            return ["item-42"]

    text, metrics = await evaluation.agent_loop(
        fake_client,
        "claude-test",
        "Run parallel tools",
        [],
        _FakeConnection(),
    )

    assert text == "<response>done</response>"
    assert tool_calls == [("search_docs", {"q": "alpha"}), ("fetch_item", {"id": 42})]
    assert metrics["search_docs"]["count"] == 1
    assert metrics["fetch_item"]["count"] == 1

    assert len(recorded_messages) == 2
    second_call_messages = recorded_messages[1]
    assert second_call_messages[-1]["role"] == "user"
    tool_results = second_call_messages[-1]["content"]
    assert len(tool_results) == 2
    assert [entry["tool_use_id"] for entry in tool_results] == ["toolu_01", "toolu_02"]
    assert all(entry["type"] == "tool_result" for entry in tool_results)
    assert json.loads(tool_results[0]["content"]) == {"hits": ["a"]}
    assert json.loads(tool_results[1]["content"]) == ["item-42"]


@pytest.mark.asyncio
async def test_agent_loop_collects_mixed_success_and_error_in_parallel_turn(monkeypatch: pytest.MonkeyPatch):
    """Keep collecting remaining parallel `tool_result`s when one tool call raises."""
    evaluation = _load_evaluation_module(monkeypatch)

    responses = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                _tool_use_block("toolu_ok", "good_tool", {"x": 1}),
                _tool_use_block("toolu_err", "bad_tool", {"x": 2}),
            ],
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[_text_block("<response>recovered</response>")],
        ),
    ]
    captured_turns: list[list[dict]] = []

    class _FakeMessages:
        def create(self, **kwargs):
            captured_turns.append(json.loads(json.dumps(kwargs["messages"], default=str)))
            return responses.pop(0)

    class _MixedConnection:
        async def call_tool(self, name: str, _tool_input: dict):
            if name == "bad_tool":
                raise RuntimeError("boom")
            return {"status": "ok"}

    text, metrics = await evaluation.agent_loop(
        SimpleNamespace(messages=_FakeMessages()),
        "claude-test",
        "Mixed turn",
        [],
        _MixedConnection(),
    )

    assert text == "<response>recovered</response>"
    assert metrics["good_tool"]["count"] == 1
    assert metrics["bad_tool"]["count"] == 1

    tool_results = captured_turns[1][-1]["content"]
    assert [entry["tool_use_id"] for entry in tool_results] == ["toolu_ok", "toolu_err"]
    assert "is_error" not in tool_results[0]
    assert json.loads(tool_results[0]["content"]) == {"status": "ok"}
    assert tool_results[1].get("is_error") is True
    assert "Error executing tool bad_tool: boom" in tool_results[1]["content"]


@pytest.mark.asyncio
async def test_agent_loop_serializes_pydantic_style_tool_result_objects(
    monkeypatch: pytest.MonkeyPatch,
):
    """Serialize MCP content models via `model_dump()` and `default=str` without false tool errors."""
    evaluation = _load_evaluation_module(monkeypatch)

    responses = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                _tool_use_block("toolu_mcp", "read_item", {"id": 1}),
            ],
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[_text_block("<response>42</response>")],
        ),
    ]
    captured_turns: list[list[dict]] = []

    class _FakeMessages:
        def create(self, **kwargs):
            captured_turns.append(json.loads(json.dumps(kwargs["messages"], default=str)))
            return responses.pop(0)

    class _FakeContentModel:
        def __init__(self, payload: dict):
            self._payload = payload

        def model_dump(self) -> dict:
            return self._payload

    class _PydanticConnection:
        async def call_tool(self, _name: str, _tool_input: dict):
            return [
                _FakeContentModel({"type": "text", "text": "42"}),
                _FakeContentModel({"type": "resource", "path": Path("/tmp/sample.txt")}),
            ]

    text, _metrics = await evaluation.agent_loop(
        SimpleNamespace(messages=_FakeMessages()),
        "claude-test",
        "Read MCP item",
        [],
        _PydanticConnection(),
    )

    assert text == "<response>42</response>"
    tool_results = captured_turns[1][-1]["content"]
    assert len(tool_results) == 1
    entry = tool_results[0]
    assert "is_error" not in entry
    assert "Error executing tool" not in entry["content"]
    assert json.loads(entry["content"]) == [
        {"type": "text", "text": "42"},
        {"type": "resource", "path": "/tmp/sample.txt"},
    ]


@pytest.mark.asyncio
async def test_agent_loop_breaks_cleanly_when_tool_use_stop_reason_has_no_blocks(
    monkeypatch: pytest.MonkeyPatch,
):
    """Break cleanly without raising `StopIteration` when `tool_use` turn has zero blocks."""
    evaluation = _load_evaluation_module(monkeypatch)

    api_calls = 0

    class _FakeMessages:
        def create(self, **_kwargs):
            nonlocal api_calls
            api_calls += 1
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[_text_block("No tool call emitted")],
            )

    class _UnusedConnection:
        async def call_tool(self, _name: str, _tool_input: dict):
            raise AssertionError("call_tool must not be invoked when no tool_use block exists")

    text, metrics = await evaluation.agent_loop(
        SimpleNamespace(messages=_FakeMessages()),
        "claude-test",
        "Empty tool_use turn",
        [],
        _UnusedConnection(),
    )

    assert api_calls == 1
    assert text == "No tool call emitted"
    assert metrics == {}


@pytest.mark.asyncio
async def test_evaluate_single_task_handles_empty_tool_use_content_safely(
    monkeypatch: pytest.MonkeyPatch,
):
    """Return a valid `<response>NOT_FOUND</response>` when `tool_use` returns empty `content`."""
    evaluation = _load_evaluation_module(monkeypatch)

    class _FakeMessages:
        def create(self, **_kwargs):
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[],
            )

    class _UnusedConnection:
        async def call_tool(self, _name: str, _tool_input: dict):
            raise AssertionError("call_tool must not be invoked when content is empty")

    result = await evaluation.evaluate_single_task(
        SimpleNamespace(messages=_FakeMessages()),
        "claude-test",
        {"question": "What is the answer?", "answer": "42"},
        [],
        _UnusedConnection(),
        0,
    )

    assert result["actual"] == "NOT_FOUND"
    assert result["score"] == 0
    assert result["summary"] is None
    assert result["feedback"] is None


@pytest.mark.asyncio
async def test_agent_loop_preserves_utf8_and_drops_none_fields_on_pydantic_dump(
    monkeypatch: pytest.MonkeyPatch,
):
    """Pass `mode='json', exclude_none=True` when supported and serialize JSON with `ensure_ascii=False`."""
    evaluation = _load_evaluation_module(monkeypatch)

    responses = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use_block("toolu_utf8", "read_item", {"id": 2})],
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[
                _text_block("<summary>ok</summary>"),
                _text_block("<response>完成</response>"),
            ],
        ),
    ]
    captured_turns: list[list[dict]] = []

    class _FakeMessages:
        def create(self, **kwargs):
            captured_turns.append(json.loads(json.dumps(kwargs["messages"], default=str)))
            return responses.pop(0)

    class _RichModel:
        def model_dump(self, *, mode: str = "python", exclude_none: bool = False) -> dict:
            data = {"type": "text", "text": "你好", "annotations": None, "mode": mode}
            return {k: v for k, v in data.items() if not (exclude_none and v is None)}

    class _RichConnection:
        async def call_tool(self, _name: str, _tool_input: dict):
            return [_RichModel()]

    text, _metrics = await evaluation.agent_loop(
        SimpleNamespace(messages=_FakeMessages()),
        "claude-test",
        "Check UTF-8 and multi-block response",
        [],
        _RichConnection(),
    )

    assert "<summary>ok</summary>" in text
    assert evaluation.extract_xml_content(text, "response") == "完成"
    entry = captured_turns[1][-1]["content"][0]
    assert "is_error" not in entry
    assert "你好" in entry["content"]
    assert "\\u4f60\\u597d" not in entry["content"]
    assert json.loads(entry["content"]) == [{"type": "text", "text": "你好", "mode": "json"}]


@pytest.mark.asyncio
async def test_agent_loop_serialization_error_does_not_mark_tool_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """Fall back to `str(tool_result)` without setting `is_error=True` if serialization raises."""
    evaluation = _load_evaluation_module(monkeypatch)

    responses = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use_block("toolu_bad_dump", "read_item", {"id": 3})],
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[_text_block("<response>ok</response>")],
        ),
    ]
    captured_turns: list[list[dict]] = []

    class _FakeMessages:
        def create(self, **kwargs):
            captured_turns.append(json.loads(json.dumps(kwargs["messages"], default=str)))
            return responses.pop(0)

    class _BrokenDump:
        def model_dump(self) -> dict:
            raise RuntimeError("dump failed")

        def __str__(self) -> str:
            return "fallback-string-result"

    class _BrokenDumpConnection:
        async def call_tool(self, _name: str, _tool_input: dict):
            return _BrokenDump()

    text, _metrics = await evaluation.agent_loop(
        SimpleNamespace(messages=_FakeMessages()),
        "claude-test",
        "Check serialization fallback",
        [],
        _BrokenDumpConnection(),
    )

    assert text == "<response>ok</response>"
    entry = captured_turns[1][-1]["content"][0]
    assert "is_error" not in entry
    assert entry["content"] == "fallback-string-result"
