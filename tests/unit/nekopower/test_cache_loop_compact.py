# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for PR 1: prompt-cache infrastructure, stream/loop recovery, and compaction."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config import nekopower_settings as settings
from config.prompts import prompts_nekopower as texts
from config.providers import get_cache_kwargs
from nekopower.host.task_agents import TaskAgentManager
from nekopower.runtime.context import (
    SUMMARY_FALLBACK,
    ContextManager,
    ContextPolicy,
    Row,
    SummaryUsedToolError,
    Transcript,
    cap_parallel_batch_results,
    extract_compaction_summary,
    frame_summary,
    kind_of,
    retain_recent_images,
)
from nekopower.runtime.loop.harness import AgentHarness, AgentHarnessConfig, SimpleCancellationToken
from nekopower.runtime.loop.messages import (
    AssistantMessage,
    CustomMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from nekopower.runtime.loop.provider_events import AssistantDoneEvent, AssistantErrorEvent
from nekopower.runtime.loop.tool_history import (
    INTERRUPTED_TOOL_RUNNING,
    INTERRUPTED_TOOL_SKIPPED,
    INTERRUPTED_TOOL_UNKNOWN,
    repair_tool_history,
)
from nekopower.runtime.model import (
    ContextOverflowError,
    LLMClientProvider,
    parse_combined_overflow,
)
from nekopower.runtime.protocol import ModelConfig
from nekopower.runtime.tools.base import SpillStore
from tests.unit.nekopower.fakes import ScriptedLLM, call, finish, make_spec, open_session, say, think, usage
from utils.llm_client.anthropic_client import ChatAnthropic, _replay_context_key
from utils.llm_client.cache_control import (
    _apply_anthropic_cache_control,
    _inject_cache_control,
    _is_cache_control_rejection,
    _strip_anthropic_cache_control,
)
from utils.llm_client.messages import LLMStreamChunk


# ---------------------------------------------------------------------------
# Batch 1: Prompt cache breakpoints (Anthropic & OpenAI-compat)
# ---------------------------------------------------------------------------


def test_anthropic_provider_registers_body_cache_control() -> None:
    kw = get_cache_kwargs("https://api.anthropic.com/v1")
    assert kw["enable_cache_control"] is True


def test_anthropic_cache_control_marks_system_tools_and_rolling_tail() -> None:
    payload = {
        "model": "claude-sonnet-4-5",
        "system": "You are an assistant.",
        "tools": [{"name": "read", "input_schema": {}}, {"name": "edit", "input_schema": {}}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hmm", "signature": "sig"},
                    {"type": "text", "text": "world"},
                ],
            },
            {"role": "user", "content": [{"type": "text", "text": "next"}]},
        ],
    }
    marked = _apply_anthropic_cache_control(payload, skip_cache_write=False)
    assert marked["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in marked["tools"][0]
    assert marked["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert marked["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # Original payload is not mutated.
    assert isinstance(payload["system"], str)

    # skip_cache_write marks the penultimate message instead of the final one.
    skipped = _apply_anthropic_cache_control(payload, skip_cache_write=True)
    assert "cache_control" not in skipped["messages"][-1]["content"][-1]
    assert skipped["messages"][-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in skipped["messages"][-2]["content"][0]  # thinking never marked

    # Stripping removes all cache_control markers cleanly.
    stripped = _strip_anthropic_cache_control(marked)
    assert "cache_control" not in json.dumps(stripped)


def test_replay_context_key_ignores_moving_cache_control_markers() -> None:
    base_payload = {
        "model": "claude-sonnet-4-5",
        "system": "System",
        "tools": [{"name": "read", "input_schema": {}}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "step 1"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ],
    }
    marked_a = _apply_anthropic_cache_control(base_payload, skip_cache_write=False)
    marked_b = _apply_anthropic_cache_control(base_payload, skip_cache_write=True)
    assert _replay_context_key(marked_a, marked_a["messages"]) == _replay_context_key(
        marked_b, marked_b["messages"]
    )
    assert _replay_context_key(base_payload, base_payload["messages"]) == _replay_context_key(
        marked_a, marked_a["messages"]
    )


def test_openai_inject_cache_control_rolling_tail_and_skip_cache_write() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    normal = _inject_cache_control(messages, rolling_tail=True, skip_cache_write=False)
    assert normal[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert normal[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert isinstance(normal[2]["content"], str)

    skipped = _inject_cache_control(messages, rolling_tail=True, skip_cache_write=True)
    assert skipped[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert skipped[2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert isinstance(skipped[-1]["content"], str)


async def test_anthropic_client_downgrades_cache_control_on_400_rejection() -> None:
    class _BadRequest(RuntimeError):
        status_code = 400

    assert _is_cache_control_rejection(_BadRequest("Unknown field: cache_control"))
    assert not _is_cache_control_rejection(_BadRequest("Invalid API key"))

    seen_payloads: list[dict[str, Any]] = []

    class _FakeMessagesAPI:
        async def create(self, **kwargs: Any) -> Any:
            seen_payloads.append(kwargs)
            if len(seen_payloads) == 1:
                raise _BadRequest("cache_control is not supported by this proxy")
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                stop_reason="end_turn",
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=2,
                    cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                ),
            )

    client = object.__new__(ChatAnthropic)
    client.model = "claude-sonnet-4-5"
    client.max_tokens = 128
    client.extra_body = None
    client.tools = None
    client.tool_choice = None
    client.enable_cache_control = True
    client._cache_control_rejected = False
    client._aclient = SimpleNamespace(messages=_FakeMessagesAPI())

    resp = await client.ainvoke([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
    assert resp.content == "ok"
    assert client._cache_control_rejected is True
    assert len(seen_payloads) == 2
    assert "cache_control" in json.dumps(seen_payloads[0])
    assert "cache_control" not in json.dumps(seen_payloads[1])


# ---------------------------------------------------------------------------
# Batch 1: Screenshot hysteresis + unseen placeholder + repeated image notice
# ---------------------------------------------------------------------------


def test_screenshot_hysteresis_preserves_prefix_until_threshold_then_batch_evicts() -> None:
    def _shot(i: int) -> list[Any]:
        return [
            AssistantMessage(content=[ToolCall(id=f"c{i}", name="computer", arguments={"action": "screenshot"})]),
            ToolResultMessage(
                tool_call_id=f"c{i}",
                tool_name="computer",
                content=[TextContent(text=f"shot {i}"), ImageContent(data=f"IMG{i}", mime_type="image/jpeg")],
            ),
        ]

    # 6 screenshots stay untouched when keep=3 and evict_at=7.
    msgs_6 = [UserMessage(content="watch"), *[m for i in range(1, 7) for m in _shot(i)]]
    rows_6 = [Row(f"e{idx}", kind_of(m), m) for idx, m in enumerate(msgs_6)]
    assert retain_recent_images(rows_6, {"computer": 3}, {"computer": "shots"}, evict_at={"computer": 7}) is None

    # Adding the 7th screenshot triggers batch eviction down to 3.
    msgs_7 = [*msgs_6, *_shot(7)]
    rows_7 = [Row(f"e{idx}", kind_of(m), m) for idx, m in enumerate(msgs_7)]
    evicted = retain_recent_images(rows_7, {"computer": 3}, {"computer": "shots"}, evict_at={"computer": 7})
    assert evicted is not None
    tool_rows = [r for r in evicted if isinstance(r.message, ToolResultMessage)]
    has_img = [any(isinstance(b, ImageContent) for b in r.message.content) for r in tool_rows]
    assert has_img == [False, False, False, False, True, True, True]
    assert tool_rows[0].message.content[1].text == texts.IMAGE_OMITTED_PLACEHOLDER


def test_unseen_evicted_images_after_last_assistant_get_distinct_placeholder() -> None:
    rows = [
        Row("e0", "human", UserMessage(content="look")),
        Row(
            "e1",
            "assistant",
            AssistantMessage(
                content=[
                    ToolCall(id="c1", name="computer", arguments={}),
                    ToolCall(id="c2", name="computer", arguments={}),
                ]
            ),
        ),
        Row(
            "e2",
            "tool",
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="computer",
                content=[TextContent(text="first"), ImageContent(data="AAA", mime_type="image/jpeg")],
            ),
        ),
        Row(
            "e3",
            "tool",
            ToolResultMessage(
                tool_call_id="c2",
                tool_name="computer",
                content=[TextContent(text="second"), ImageContent(data="BBB", mime_type="image/jpeg")],
            ),
        ),
    ]
    evicted = retain_recent_images(rows, {"computer": 1})
    assert evicted is not None
    assert evicted[2].message.content[1].text == texts.IMAGE_OMITTED_UNSEEN_PLACEHOLDER


def test_image_evict_at_respects_single_image_cap_and_default_hysteresis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TASK_AGENT_SCREENSHOTS_EVICT_AT", 7)
    assert TaskAgentManager._image_evict_at({"computer": 3}) == {"computer": 7}
    assert TaskAgentManager._image_evict_at({"computer": 1}) == {"computer": 2}


def test_wake_min_delay_and_late_notice_constants() -> None:
    assert settings.WAKE_MIN_DELAY_SECONDS == 120.0
    assert settings.WAKE_LATE_NOTICE_SECONDS == 120.0


# ---------------------------------------------------------------------------
# Batch 2A: 5-state stream recovery, 529 retry, 404 fallback, combined overflow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        ("input length and max_tokens exceed context limit: 195000 + 8192 > 200000", (195000, 200000)),
        (
            "This model's maximum context length is 128000 tokens. However, you requested 130000 tokens "
            "(122000 in the messages, 8000 in the completion).",
            (122000, 128000),
        ),
        ("prompt is too long: 210000 tokens > 200000 maximum", None),
        ("input length and max_tokens exceed context limit: 205000 + 8192 > 200000", None),
    ],
)
def test_parse_combined_overflow(msg: str, expected: tuple[int, int] | None) -> None:
    assert parse_combined_overflow(msg) == expected


async def test_mid_stream_drop_salvages_complete_tool_calls_and_text_and_retries_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "TASK_AGENT_STREAM_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr(settings, "TASK_AGENT_STREAM_MAX_RETRIES", 2)

    async def _run(turns: list[list[Any]]) -> tuple[list[Any], ScriptedLLM]:
        llm = ScriptedLLM(turns)

        async def factory() -> ScriptedLLM:
            return llm

        provider = LLMClientProvider(ModelConfig(model="fake-model"), client_factory=factory)
        events = [
            e
            async for e in provider.stream_response(
                model="fake-model", system="S", messages=[UserMessage(content="go")], tools=[]
            )
        ]
        return events, llm

    # 1. Drop after complete tool call -> salvage as toolUse.
    events_tool, llm_tool = await _run(
        [[call("read", {"file_path": "a.py"}, "c1"), ConnectionResetError("peer reset")]]
    )
    assert isinstance(events_tool[-1], AssistantDoneEvent)
    assert events_tool[-1].reason == "toolUse"
    assert events_tool[-1].message.tool_calls[0].name == "read"
    assert len(llm_tool.requests) == 1

    # 2. Drop after visible text -> salvage as length cutoff.
    events_text, llm_text = await _run([[say("partial answer"), ConnectionResetError("eof")]])
    assert isinstance(events_text[-1], AssistantDoneEvent)
    assert events_text[-1].reason == "length"
    assert events_text[-1].message.text == "partial answer"
    assert len(llm_text.requests) == 1

    # 3. Drop during reasoning-only -> clean reset & retry.
    events_think, llm_think = await _run(
        [[think("thinking..."), ConnectionResetError("reset")], [say("recovered"), finish()]]
    )
    assert isinstance(events_think[-1], AssistantDoneEvent)
    assert events_think[-1].reason == "stop"
    assert events_think[-1].message.text == "recovered"
    assert len(llm_think.requests) == 2


async def test_stream_idle_watchdog_triggers_retry_and_404_falls_back_to_non_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "TASK_AGENT_STREAM_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr(settings, "TASK_AGENT_STREAM_IDLE_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(settings, "TASK_AGENT_STREAM_MAX_RETRIES", 2)

    async def stall() -> None:
        await asyncio.sleep(5.0)

    llm = ScriptedLLM([[stall], [say("after stall"), finish()]])

    async def factory() -> ScriptedLLM:
        return llm

    provider = LLMClientProvider(ModelConfig(model="fake-model"), client_factory=factory)
    events = [
        e
        async for e in provider.stream_response(
            model="fake-model", system="S", messages=[UserMessage(content="go")], tools=[]
        )
    ]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.text == "after stall"
    assert len(llm.requests) == 2

    # Streaming 404 falls back once to ainvoke_raw.
    class _NotFound(RuntimeError):
        status_code = 404

    class _FallbackClient:
        def __init__(self) -> None:
            self.raw_calls = 0

        async def astream(self, messages: list[dict[str, Any]], **overrides: Any):  # noqa: ANN201
            del messages, overrides
            if False:
                yield LLMStreamChunk(content="")
            raise _NotFound("stream endpoint not found")

        async def ainvoke_raw(self, messages: list[dict[str, Any]], **overrides: Any) -> Any:
            del messages, overrides
            self.raw_calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="non-stream ok", reasoning_content=None, tool_calls=[]),
                        finish_reason="stop",
                    )
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            )

    fb = _FallbackClient()

    async def fb_factory() -> _FallbackClient:
        return fb

    fb_provider = LLMClientProvider(ModelConfig(model="fake-model"), client_factory=fb_factory)
    fb_events = [
        e
        async for e in fb_provider.stream_response(
            model="fake-model", system="S", messages=[UserMessage(content="go")], tools=[]
        )
    ]
    assert isinstance(fb_events[-1], AssistantDoneEvent)
    assert fb_events[-1].message.text == "non-stream ok"
    assert fb.raw_calls == 1


async def test_overloaded_529_retries_before_succeeding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TASK_AGENT_STREAM_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr(settings, "TASK_AGENT_STREAM_MAX_RETRIES", 2)

    class _Overloaded(RuntimeError):
        status_code = 529

    llm = ScriptedLLM([[_Overloaded("overloaded_error")], [say("ok now"), finish()]])

    async def factory() -> ScriptedLLM:
        return llm

    provider = LLMClientProvider(ModelConfig(model="fake-model"), client_factory=factory)
    events = [
        e
        async for e in provider.stream_response(
            model="fake-model", system="S", messages=[UserMessage(content="hi")], tools=[]
        )
    ]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.text == "ok now"
    assert len(llm.requests) == 2


# ---------------------------------------------------------------------------
# Batch 2A: Combined overflow clamp, 4-state interrupts, cutoff & empty nudges
# ---------------------------------------------------------------------------


async def test_combined_overflow_clamps_max_tokens_without_compacting(tmp_path: Path) -> None:
    model = ModelConfig(model="fake-model", api_key="k", context_window=200_000, max_output_tokens=16_000)
    llm = ScriptedLLM(
        [
            [RuntimeError("input length and max_tokens exceed context limit: 192000 + 16000 > 200000")],
            [say("Clamped and succeeded."), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path, model=model), llm)
    try:
        mark = len(events.events)
        session.prompt("Run task.")
        ended = await events.wait_ended(start=mark)
        assert ended["reason"] == "completed"
        assert ended["text"] == "Clamped and succeeded."
        # No compaction happened and session window was not shrunk.
        assert events.of("compacted") == []
        assert session._window == 200_000
        assert len(llm.requests) == 2
        assert llm.overrides[1]["max_completion_tokens"] == 7_000
    finally:
        await session.close()


async def test_cutoff_nudges_by_scenario_and_exhaustion_fails_explicitly(tmp_path: Path) -> None:
    # Truncated tool call triggers tool-specific nudge.
    truncated_tool_chunk = LLMStreamChunk(
        content="",
        tool_call_deltas=[
            {"index": 0, "id": "c1", "type": "function", "function": {"name": "write", "arguments": '{"path": "a'}}
        ],
    )
    llm = ScriptedLLM(
        [
            [truncated_tool_chunk, finish("length")],
            [say("Done cleanly."), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm)
    try:
        mark = len(events.events)
        session.prompt("Write a file.")
        ended = await events.wait_ended(start=mark)
        assert ended["reason"] == "completed"
        assert "write" in json.dumps(llm.requests[1])
        assert "incomplete call was discarded" in json.dumps(llm.requests[1])
    finally:
        await session.close()

    # Thinking-only cutoff triggers thinking-specific nudge; exhausting 3 nudges fails explicitly.
    llm_fail = ScriptedLLM(
        [
            [think("long thoughts"), finish("length")],
            [say("part 1"), finish("length")],
            [say("part 2"), finish("length")],
            [say("part 3"), finish("length")],
        ]
    )
    s2_dir = tmp_path / "s2"
    s2_dir.mkdir()
    session2, events2 = await open_session(make_spec(s2_dir, session_id="s2"), llm_fail)
    try:
        mark = len(events2.events)
        session2.prompt("Think hard.")
        ended2 = await events2.wait_ended(start=mark)
        assert "during reasoning" in json.dumps(llm_fail.requests[1])
        assert ended2["reason"] == "error"
        assert "output token limit repeatedly" in (ended2.get("error") or "")
    finally:
        await session2.close()


async def test_empty_thinking_only_turn_and_unnamed_tool_turn_trigger_recovery_nudges(tmp_path: Path) -> None:
    unnamed_tool = LLMStreamChunk(
        content="",
        tool_call_deltas=[{"index": 0, "id": "c1", "type": "function", "function": {"name": "", "arguments": "{}"}}],
    )
    llm = ScriptedLLM(
        [
            [unnamed_tool, finish("tool_calls")],
            [think("forgot to speak"), finish("stop")],
            [say("Actual answer."), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm)
    try:
        mark = len(events.events)
        session.prompt("Do something.")
        ended = await events.wait_ended(start=mark)
        assert ended["reason"] == "completed"
        assert ended["text"] == "Actual answer."
        assert texts.INVALID_TOOL_TURN_NUDGE in json.dumps(llm.requests[1])
        assert texts.EMPTY_COMPLETION_NUDGE in json.dumps(llm.requests[2])
    finally:
        await session.close()


def test_four_state_interrupted_tool_history_and_legacy_dedup() -> None:
    c = ToolCall(id="c1", name="bash", arguments={"command": "echo hi"})
    assistant = AssistantMessage(content=[c])
    legacy = ToolResultMessage(
        tool_call_id="c1", tool_name="bash", content="Tool call interrupted by user", is_error=True
    )
    skipped = ToolResultMessage(
        tool_call_id="c1", tool_name="bash", content=INTERRUPTED_TOOL_SKIPPED, is_error=True
    )
    running = ToolResultMessage(
        tool_call_id="c1", tool_name="bash", content=INTERRUPTED_TOOL_RUNNING, is_error=True
    )
    real = ToolResultMessage(tool_call_id="c1", tool_name="bash", content="hi", is_error=False)

    # All synthetic interruption texts (including legacy) yield to real results.
    for synthetic in (legacy, skipped, running):
        repaired = repair_tool_history((assistant, synthetic, real))
        assert repaired.messages == (assistant, real)

    # Missing result synthesizes outcome-unknown text.
    missing = repair_tool_history((assistant,))
    assert missing.messages[-1].text == INTERRUPTED_TOOL_UNKNOWN


# ---------------------------------------------------------------------------
# Batch 2B: Compaction XML stripping, 3-tier ladder, circuit breaker, batch cap
# ---------------------------------------------------------------------------


def test_extract_compaction_summary_and_frame_honesty_notes() -> None:
    raw = "<analysis>scratchpad thoughts</analysis>\n<summary>\n## Current Work\n- clean\n</summary>"
    extracted = extract_compaction_summary(raw)
    assert extracted == "## Current Work\n- clean"
    assert "scratchpad" not in extracted

    framed = frame_summary(extracted, head_truncated=True, has_retained_tail=True)
    assert framed.startswith(texts.CHECKPOINT_PREAMBLE)
    assert texts.CHECKPOINT_RETAINED_TAIL_NOTE in framed
    assert texts.CHECKPOINT_HEAD_TRUNCATED_NOTE in framed
    assert framed.rstrip().endswith(texts.SUMMARY_CLOSE_TAG)


async def test_compaction_three_tier_retry_ladder(tmp_path: Path) -> None:
    policy = ContextPolicy(window=8_000, max_output=1_000)
    calls: list[tuple[int, bool, str | None]] = []

    async def summarize(
        messages: list[Any], max_tokens: int, *, no_tools: bool = False, retry_note: str | None = None
    ) -> tuple[str, Usage | None]:
        del max_tokens
        calls.append((len(messages), no_tools, retry_note))
        if len(calls) == 1:
            raise SummaryUsedToolError("model called a tool")
        return "<analysis>verify</analysis><summary>## Current Work\n- recovered</summary>", Usage(input=10, output=5)

    seq = iter(range(1, 10_000))
    manager = ContextManager(
        policy=policy,
        spill=SpillStore(tmp_path / "outputs"),
        summarize=summarize,
        new_entry_id=lambda: f"sum-{next(seq)}",
    )
    prose = " ".join(f"detail{j}" for j in range(400))
    messages: list[Any] = [UserMessage(content="build it")]
    for i in range(1, 9):
        messages.append(AssistantMessage(content=f"Step {i}: {prose}"))
        messages.append(UserMessage(content=f"Continue step {i}."))
    transcript = Transcript(messages, lambda: f"m-{next(seq)}")
    compactions = await manager.fit(transcript, force=True)
    assert len(compactions) == 1
    assert calls[0][1] is False and calls[1][1] is True and calls[1][2] == texts.SUMMARY_NO_TOOLS_RETRY_PROMPT
    assert "## Current Work\n- recovered" in compactions[0].stored_summary
    assert "<analysis>" not in compactions[0].stored_summary


async def test_compaction_tier3_overflow_drops_oldest_and_notifies_window_learner(tmp_path: Path) -> None:
    policy = ContextPolicy(window=16_000, max_output=1_000)
    learned: list[tuple[str, int]] = []
    calls: list[int] = []

    async def summarize(
        messages: list[Any], max_tokens: int, *, no_tools: bool = False, retry_note: str | None = None
    ) -> tuple[str, Usage | None]:
        del max_tokens, no_tools, retry_note
        calls.append(len(messages))
        if len(calls) == 1:
            raise ContextOverflowError("prompt is too long: 17000 tokens > 16000 maximum")
        return "## Current Work\n- trimmed ok", Usage(input=5, output=5)

    seq = iter(range(1, 10_000))
    manager = ContextManager(
        policy=policy,
        spill=SpillStore(tmp_path / "outputs"),
        summarize=summarize,
        new_entry_id=lambda: f"sum-{next(seq)}",
        on_summary_overflow=lambda said, sent: learned.append((said, sent)),
    )
    prose = " ".join(f"analysis{j}" for j in range(600))
    messages: list[Any] = [UserMessage(content="go")]
    for i in range(1, 11):
        messages.append(AssistantMessage(content=f"Turn {i}: {prose}"))
        messages.append(UserMessage(content=f"Proceed {i}."))
    transcript = Transcript(messages, lambda: f"m-{next(seq)}")
    compactions = await manager.fit(transcript, force=True)
    assert len(compactions) == 1
    assert len(calls) == 2 and calls[1] < calls[0]
    assert len(learned) == 1
    assert texts.CHECKPOINT_HEAD_TRUNCATED_NOTE in compactions[0].stored_summary


def test_parallel_batch_tool_results_are_capped_together(tmp_path: Path) -> None:
    spill = SpillStore(tmp_path / "outputs")
    big = " ".join(f"item{i}" for i in range(4_000))
    rows = [
        Row("e0", "human", UserMessage(content="search all")),
        Row(
            "e1",
            "assistant",
            AssistantMessage(
                content=[
                    ToolCall(id="c1", name="grep", arguments={}),
                    ToolCall(id="c2", name="grep", arguments={}),
                    ToolCall(id="c3", name="grep", arguments={}),
                ]
            ),
        ),
        Row("e2", "tool", ToolResultMessage(tool_call_id="c1", tool_name="grep", content=big)),
        Row("e3", "tool", ToolResultMessage(tool_call_id="c2", tool_name="grep", content=big)),
        Row("e4", "tool", ToolResultMessage(tool_call_id="c3", tool_name="grep", content=big)),
    ]
    capped = cap_parallel_batch_results(rows, budget=4_000, spill=spill)
    assert capped is not None
    assert all("Full formatted result stored at:" in r.message.text for r in capped[2:])


async def test_oversized_user_input_spills_with_anti_guess_notice(tmp_path: Path) -> None:
    model = ModelConfig(model="fake-model", api_key="k", context_window=16_000, max_output_tokens=1_000)
    llm = ScriptedLLM([[say("Got it."), finish()]])
    session, events = await open_session(make_spec(tmp_path, model=model), llm)
    try:
        huge = "BEGIN " + " ".join(f"word{i}" for i in range(15_000)) + " END"
        mark = len(events.events)
        session.prompt(huge)
        ended = await events.wait_ended(start=mark)
        assert ended["reason"] == "completed"
        sent = json.dumps(llm.requests[0], ensure_ascii=False)
        assert "Do NOT guess or fabricate the omitted middle" in sent
        assert list((Path(session.spec.session_dir) / "outputs").iterdir())
    finally:
        await session.close()


async def test_post_compaction_rearms_runtime_context_and_circuit_breaker_trips(tmp_path: Path) -> None:
    model = ModelConfig(model="fake-model", api_key="k", context_window=32_000, max_output_tokens=2_000)
    session, _events = await open_session(make_spec(tmp_path, model=model), ScriptedLLM())
    try:
        session._context_due = False
        await session._after_compaction(tell=False)
        assert session._context_due is True

        # Simulate 3 consecutive rapid auto-compactions -> circuit breaker trips.
        for i in range(settings.TASK_AGENT_AUTO_COMPACT_BREAKER_COUNT):
            session._turns_since_last_compaction = 1
            comp = SimpleNamespace(
                summary_row=Row(f"sum-{i}", "summary", UserMessage(content="sum")),
                stored_summary="sum",
                replaced_entry_ids=[],
                tokens_before=20_000,
                usage=None,
                complete=True,
            )
            session._persist_compaction(comp)  # type: ignore[arg-type]
        assert session._compaction_breaker_tripped is True

        # A new human message resets the circuit breaker.
        human = UserMessage(content="new instruction")
        session._on_message(human)
        assert session._compaction_breaker_tripped is False
        assert session._rapid_compactions == 0
    finally:
        await session.close()


async def test_model_switch_precompacts_with_previous_larger_window_model(tmp_path: Path) -> None:
    from nekopower.host.ledger import Session
    from nekopower.host.role_models import Choice
    from nekopower.host.task_agents.idle_compact import IdleCompactMixin
    from nekopower.runtime.protocol import ModelConfig

    compacted_calls: list[dict[str, Any]] = []

    class _FakeRuntime:
        async def start(self, spec: Any) -> None:
            compacted_calls.append({"action": "start", "model": spec.model.model, "window": spec.model.context_window})
            manager._service[spec.session_id].put_nowait({"type": "started", "session_id": spec.session_id})

        async def compact(self, session_id: str) -> None:
            compacted_calls.append({"action": "compact", "session_id": session_id})
            manager._service[session_id].put_nowait({"type": "compacted", "session_id": session_id, "count": 1})
            manager._service[session_id].put_nowait({"type": "ended", "session_id": session_id, "reason": "completed"})

        async def close(self, session_id: str) -> None:
            compacted_calls.append({"action": "close", "session_id": session_id})
            manager._service[session_id].put_nowait({"type": "ended", "session_id": session_id, "reason": "closed"})

    class _FakeLedger:
        def __init__(self) -> None:
            self.ended_compacting: list[tuple[str, bool]] = []

        async def end_compacting(self, session_id: str, *, compacted: bool) -> None:
            self.ended_compacting.append((session_id, compacted))

    class _FakeManager(IdleCompactMixin):
        def __init__(self) -> None:
            self._closed = False
            self._switched_on = None
            self._gateway = None
            self._pump = object()
            self._service: dict[str, asyncio.Queue[dict[str, Any]]] = {}
            self._usage_locks: dict[str, asyncio.Lock] = {}
            self._context_written: dict[str, int] = {}
            self._context_said = 0
            self._ledger = _FakeLedger()
            self._fake_runtime = _FakeRuntime()
            self._runtimes = SimpleNamespace(builtin=lambda: self._fake_runtime)

        async def _resolve_model_by_ident(self, target_ident: str) -> Any | None:
            if target_ident == "old-ident":
                return Choice(
                    slot="slot_big",
                    agent=False,
                    name="big-model",
                    ident="old-ident",
                    free=False,
                    config={"model": "big-model", "base_url": "https://api.example.com/v1", "api_key": "k"},
                )
            return None

        async def _model_of(self, config: dict[str, Any]) -> ModelConfig:
            return ModelConfig(model=config["model"], context_window=200_000)

        async def _spec(self, commission: Any, kept: Any, model: Any, **kwargs: Any) -> Any:
            del commission, kwargs
            return SimpleNamespace(session_id=kept.id, model=model)

        async def _charge(self, free: bool, key: str) -> bool:
            del free, key
            return True

        async def _note_usage(self, session_id: str, commission_id: Any, event: dict[str, Any]) -> None:
            del session_id, commission_id, event

        def _settle_usage_of(self, session_id: str) -> None:
            del session_id

    manager = _FakeManager()
    kept = Session(
        id="sess-1",
        name="demo",
        lanlan_name="Lan",
        kind="project",
        project="demo",
        workdir=str(tmp_path),
        session_dir=str(tmp_path),
        runtime="builtin",
        parent_id=None,
        forked_from=None,
        status="closed",
        created_at=0.0,
        last_active_at=0.0,
        compacted_at=None,
        context_tokens=28_000,
        model_ident="new-ident",
        model_was="old-ident",
    )
    new_choice = Choice(slot="slot_small", agent=False, name="small-model", ident="new-ident", free=False)
    new_model = ModelConfig(model="small-model", context_window=32_000)
    commission = SimpleNamespace(id="c-1")
    template = SimpleNamespace(calls_nothing=False)

    did_compact = await manager._maybe_precompact_for_model_switch(
        commission,
        kept,
        new_choice,
        new_model,
        workdir=str(tmp_path),
        session_path=str(tmp_path),
        template=template,
    )
    assert did_compact is True
    assert compacted_calls == [
        {"action": "start", "model": "big-model", "window": 200_000},
        {"action": "compact", "session_id": "sess-1"},
        {"action": "close", "session_id": "sess-1"},
    ]
    assert manager._ledger.ended_compacting == [("sess-1", True)]

    # Below threshold (context_tokens <= 80% of new_window): skipped with zero model calls.
    compacted_calls.clear()
    short_kept = dataclasses.replace(kept, context_tokens=10_000)
    assert (
        await manager._maybe_precompact_for_model_switch(
            commission,
            short_kept,
            new_choice,
            new_model,
            workdir=str(tmp_path),
            session_path=str(tmp_path),
            template=template,
        )
        is False
    )
    assert compacted_calls == []
