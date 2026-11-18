# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file tests/test_session.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see nekoclaw/runtime/loop/LICENSE.
# Adapted to this repository: pytest-asyncio auto mode instead of anyio marks,
# imports point at nekoclaw.runtime.loop, and the fake provider lives in
# tests/unit/nekoclaw/loop_event_helpers.py.

import json
from pathlib import Path

import pytest

from nekoclaw.runtime.loop import (
    AssistantMessage,
    CustomMessage,
    ImageContent,
    ResponseTiming,
    TextContent,
    ThinkingContent,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
)
from nekoclaw.runtime.loop.messages import message_to_user, sum_usage
from nekoclaw.runtime.loop.session import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    JsonlSessionStorage,
    LabelEntry,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionJsonlError,
    SessionState,
    SessionTreeError,
    entries_from_json_lines,
    entry_from_json_line,
    entry_to_json_line,
    path_to_entry,
)


def test_session_entry_round_trips_canonical_jsonl() -> None:
    entry = MessageEntry(
        id="entry-1",
        timestamp=1,
        message=UserMessage(content="Hello", timestamp=2),
    )

    line = entry_to_json_line(entry)

    assert entry_from_json_line(line) == entry
    assert json.loads(line)["message"] == {
        "role": "user",
        "content": "Hello",
        "timestamp": 2,
    }


def test_custom_message_entry_round_trips_with_tau_persisted_naming() -> None:
    entry = CustomMessageEntry(
        id="entry-1",
        timestamp=2,
        content=[
            TextContent(text="<task-notification/>"),
            ImageContent(data="aW1hZ2U=", mime_type="image/png"),
        ],
        custom_type="subagent-notification",
        details={"id": "run-1"},
    )

    line = entry_to_json_line(entry)
    parsed = entry_from_json_line(line)

    payload = json.loads(line)
    assert payload["type"] == "custom_message"
    assert payload["custom_type"] == "subagent-notification"
    assert "customType" not in payload
    assert payload["content"][1]["mimeType"] == "image/png"
    assert parsed == entry


def test_assistant_and_tool_result_round_trip_canonical_blocks() -> None:
    assistant = MessageEntry(id="a", message=AssistantMessage(content="Hi"))
    result = MessageEntry(
        id="r",
        message=ToolResultMessage(
            tool_call_id="call-1",
            tool_name="edit",
            content="Successfully replaced 1 block.",
            details={"patch": "--- a.py\n+++ a.py"},
        ),
    )

    assistant_payload = json.loads(entry_to_json_line(assistant))["message"]
    result_payload = json.loads(entry_to_json_line(result))["message"]

    assert assistant_payload["content"][0]["text"] == "Hi"
    assert assistant_payload["usage"]["totalTokens"] == 0
    assert "timing" not in assistant_payload
    assert result_payload["role"] == "toolResult"
    assert result_payload["toolName"] == "edit"
    assert entry_from_json_line(entry_to_json_line(assistant)) == assistant
    assert entry_from_json_line(entry_to_json_line(result)) == result


def test_assistant_response_timing_round_trips_jsonl() -> None:
    entry = MessageEntry(
        id="timed",
        message=AssistantMessage(
            content="Hi",
            timing=ResponseTiming(time_to_first_output_ms=250, total_duration_ms=1000),
        ),
    )

    line = entry_to_json_line(entry)

    assert entry_from_json_line(line) == entry
    assert json.loads(line)["message"]["timing"] == {
        "timeToFirstOutputMs": 250,
        "totalDurationMs": 1000,
    }


def test_tool_result_image_round_trips_jsonl() -> None:
    entry = MessageEntry(
        id="image",
        message=ToolResultMessage(
            tool_call_id="call-1",
            tool_name="read",
            content=[
                TextContent(text="Read image file [image/png]"),
                ImageContent(data="aW1hZ2U=", mime_type="image/png"),
            ],
        ),
    )

    line = entry_to_json_line(entry)

    assert entry_from_json_line(line) == entry
    assert json.loads(line)["message"]["content"][1] == {
        "type": "image",
        "data": "aW1hZ2U=",
        "mimeType": "image/png",
    }


def test_structured_thinking_message_round_trips_jsonl() -> None:
    entry = MessageEntry(
        id="a",
        message=AssistantMessage(
            content=[
                ThinkingContent(thinking="plan", thinking_signature="reasoning"),
                TextContent(text="done"),
            ]
        ),
    )

    parsed = entry_from_json_line(entry_to_json_line(entry))

    assert parsed == entry
    payload = json.loads(entry_to_json_line(entry))["message"]
    assert [block["type"] for block in payload["content"]] == ["thinking", "text"]
    assert payload["content"][0]["thinkingSignature"] == "reasoning"


def test_legacy_assistant_message_migrates_to_ordered_blocks() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "a",
            "timestamp": 1,
            "message": {
                "role": "assistant",
                "content": "Reading.",
                "tool_calls": [
                    {"id": "call-1", "name": "read", "arguments": {"path": "README.md"}}
                ],
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, AssistantMessage)
    assert entry.message.text == "Reading."
    assert entry.message.tool_calls[0].name == "read"
    rewritten = json.loads(entry_to_json_line(entry))["message"]
    assert "tool_calls" not in rewritten
    assert [block["type"] for block in rewritten["content"]] == ["text", "toolCall"]


def test_assistant_message_with_legacy_null_usage_cost_migrates() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "a",
            "timestamp": 1,
            "message": {
                "role": "assistant",
                "content": "Done.",
                "usage": {
                    "input": 10,
                    "output": 2,
                    "cache_read": 0,
                    "cache_write": 0,
                    "total_tokens": 12,
                    "cost": None,
                },
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, AssistantMessage)
    assert entry.message.usage.total_tokens == 12
    assert entry.message.usage.cost.total == 0.0
    rewritten = json.loads(entry_to_json_line(entry))["message"]
    assert rewritten["usage"]["cost"]["total"] == 0.0


def test_legacy_tool_message_migrates_and_preserves_data() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "tool",
            "timestamp": 1,
            "message": {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "edit",
                "content": "changed",
                "ok": False,
                "error": "failed",
                "data": {"patch": "diff"},
                "details": {"line": 12},
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, ToolResultMessage)
    assert entry.message.role == "toolResult"
    assert entry.message.tool_name == "edit"
    assert entry.message.is_error is True
    assert entry.message.text == "changed"
    assert entry.message.details == {"patch": "diff", "line": 12}
    rewritten = json.loads(entry_to_json_line(entry))["message"]
    assert rewritten["role"] == "toolResult"
    assert not {"name", "ok", "error", "data", "tool_call_id"} & rewritten.keys()


@pytest.mark.parametrize("role", ["user", "custom"])
@pytest.mark.parametrize("custom_type_key", ["custom_type", "customType"])
def test_legacy_custom_message_shapes_migrate_and_replay_identically(
    role: str, custom_type_key: str
) -> None:
    legacy_message = {
        "role": role,
        "content": "<task-notification/>",
        custom_type_key: "subagent-notification",
        "display": False,
        "details": {"id": "run-1"},
        "timestamp": 2345,
    }
    legacy = json.dumps(
        {
            "type": "message",
            "id": "custom",
            "timestamp": 1,
            "message": legacy_message,
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, CustomMessageEntry)
    assert entry.parent_id is None
    assert entry.timestamp == 2.345
    assert entry.custom_type == "subagent-notification"
    assert entry.display is False
    assert entry.details == {"id": "run-1"}
    state = SessionState.from_entries([entry])
    assert state.messages == (
        CustomMessage(
            custom_type="subagent-notification",
            content="<task-notification/>",
            display=False,
            details={"id": "run-1"},
            timestamp=2345,
        ),
    )
    assert (
        message_to_user(state.messages[0]).model_dump_json()
        == UserMessage(content="<task-notification/>", timestamp=2345).model_dump_json()
    )
    rewritten = json.loads(entry_to_json_line(entry))
    assert rewritten["type"] == "custom_message"
    assert rewritten["custom_type"] == "subagent-notification"
    assert "message" not in rewritten


def test_pi_named_custom_message_entry_normalizes_to_tau_persistence() -> None:
    entry = entry_from_json_line(
        json.dumps(
            {
                "type": "custom_message",
                "id": "custom",
                "timestamp": 1,
                "customType": "extension:status",
                "content": "ready",
                "display": True,
            }
        )
    )

    assert isinstance(entry, CustomMessageEntry)
    assert entry.custom_type == "extension:status"
    assert "custom_type" in json.loads(entry_to_json_line(entry))


def test_invalid_jsonl_line_raises_useful_error() -> None:
    with pytest.raises(SessionJsonlError, match="Invalid session entry on line 3"):
        entry_from_json_line('{"type":"unknown"}', line_number=3)


async def test_jsonl_storage_appends_and_reads_entries(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "sessions" / "one.jsonl")
    first = MessageEntry(id="one", message=UserMessage(content="Hi"))
    second = LabelEntry(id="two", target_id="one", label="Greeting")

    await storage.append(first)
    await storage.append(second)

    assert await storage.read_all() == [first, second]


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
async def test_jsonl_storage_round_trips_unicode_line_separators(
    tmp_path: Path, separator: str
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    entry = MessageEntry(id="one", message=UserMessage(content=f"before{separator}after"))

    await storage.append(entry)

    assert await storage.read_all() == [entry]


async def test_jsonl_storage_reads_existing_file_with_unicode_line_separator(
    tmp_path: Path,
) -> None:
    entry = MessageEntry(id="one", message=UserMessage(content="a\u2028b"))
    path = tmp_path / "session.jsonl"
    path.write_text(entry_to_json_line(entry), encoding="utf-8")
    storage = JsonlSessionStorage(path)

    assert await storage.read_all() == [entry]


async def test_jsonl_storage_reuses_parsed_entries_after_own_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nekoclaw.runtime.loop.session import storage as storage_module

    path = tmp_path / "session.jsonl"
    storage = JsonlSessionStorage(path)
    first = MessageEntry(id="one", message=UserMessage(content="Hi"))
    second = MessageEntry(id="two", parent_id="one", message=UserMessage(content="Again"))
    third = CustomEntry(id="three", parent_id="two", namespace="ext", data={"n": 1})
    await storage.append(first)
    assert await storage.read_all() == [first]
    full_parses = 0
    original_parse = storage_module.entries_from_json_lines

    def counting_parse(lines: list[str]) -> list[object]:
        nonlocal full_parses
        full_parses += 1
        return original_parse(lines)  # type: ignore[return-value]

    monkeypatch.setattr(storage_module, "entries_from_json_lines", counting_parse)

    await storage.append(second)
    await storage.append_batch([third])
    cached = await storage.read_all()

    assert full_parses == 0
    assert cached == [first, second, third]
    assert cached[1] is not second
    assert cached == await JsonlSessionStorage(path).read_all()


async def test_jsonl_storage_rereads_after_external_writes(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    storage = JsonlSessionStorage(path)
    other_process = JsonlSessionStorage(path)
    first = MessageEntry(id="one", message=UserMessage(content="Hi"))
    second = MessageEntry(id="two", parent_id="one", message=UserMessage(content="Other"))
    third = MessageEntry(id="three", parent_id="one", message=UserMessage(content="Mine"))
    await storage.append(first)
    assert await storage.read_all() == [first]

    await other_process.append(second)
    assert await storage.read_all() == [first, second]

    # An append after an unseen external write cannot extend stale entries.
    path.write_text(entry_to_json_line(first), encoding="utf-8")
    await storage.append(third)
    assert await storage.read_all() == [first, third]


async def test_jsonl_storage_cache_matches_reread_when_append_retargets_legacy_label(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps({"type": "session_info", "id": "info", "timestamp": 1, "created_at": 1})
        + "\n"
        + json.dumps(
            {
                "type": "label",
                "id": "legacy-label",
                "parent_id": "info",
                "timestamp": 2,
                "label": "Legacy name",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    storage = JsonlSessionStorage(path)
    before = await storage.read_all()
    assert isinstance(before[-1], LabelEntry)
    assert before[-1].target_id == "info"

    await storage.append(
        MessageEntry(id="first-message", parent_id="info", message=UserMessage(content="hi"))
    )

    after = await storage.read_all()
    assert isinstance(after[1], LabelEntry)
    assert after[1].target_id == "first-message"
    assert after == await JsonlSessionStorage(path).read_all()


async def test_jsonl_storage_reads_do_not_alias_cached_entries(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    storage = JsonlSessionStorage(path)
    first = MessageEntry(id="one", message=UserMessage(content="durable"))
    await storage.append(first)

    entries = await storage.read_all()
    entries[0].message.content = "in-memory-only"

    second = MessageEntry(id="two", parent_id="one", message=UserMessage(content="again"))
    await storage.append(second)

    # Mutating a returned entry must not corrupt future cached reads.
    cached = await storage.read_all()
    assert cached[0].message.content == "durable"
    assert cached == await JsonlSessionStorage(path).read_all()


def test_session_state_replays_linear_entries() -> None:
    user = UserMessage(content="Hi", timestamp=1)
    assistant = AssistantMessage(content="Hello", timestamp=2)
    entries = [
        MessageEntry(id="user", message=user),
        ModelChangeEntry(id="model", parent_id="user", model="fake-model"),
        MessageEntry(id="assistant", parent_id="model", message=assistant),
        LabelEntry(
            id="label",
            parent_id="assistant",
            target_id="assistant",
            label="Greeting",
            timestamp=3,
        ),
        CustomEntry(id="custom", parent_id="label", namespace="test", data={"ok": True}),
        # Historical pointers deserialize but do not override the file-order tip.
        LeafEntry(id="leaf", parent_id="custom", entry_id="assistant"),
    ]

    state = SessionState.from_entries(entries)

    assert state.messages == (user, assistant)
    assert state.model == "fake-model"
    assert state.labels_by_id == {"assistant": "Greeting"}
    assert state.label_timestamps_by_id == {"assistant": 3}
    assert state.active_leaf_id == "custom"


def test_session_state_resolves_relabel_clear_and_relabel_in_file_order() -> None:
    target = MessageEntry(id="target", message=UserMessage(content="Bookmark me"))
    entries = [
        target,
        LabelEntry(
            id="first", parent_id="target", target_id="target", label=" first ", timestamp=2
        ),
        LabelEntry(id="clear", parent_id="first", target_id="target", label="", timestamp=3),
        LabelEntry(id="latest", parent_id="clear", target_id="target", label="latest", timestamp=4),
    ]

    state = SessionState.from_entries(entries)

    assert state.labels_by_id == {"target": "latest"}
    assert state.label_timestamps_by_id == {"target": 4}


def test_session_state_clear_removes_label_and_timestamp() -> None:
    entries = [
        MessageEntry(id="target", message=UserMessage(content="Bookmark me")),
        LabelEntry(id="set", parent_id="target", target_id="target", label="saved"),
        LabelEntry(id="clear", parent_id="set", target_id="target", label=None),
    ]

    state = SessionState.from_entries(entries)

    assert state.labels_by_id == {}
    assert state.label_timestamps_by_id == {}


def test_legacy_session_label_migrates_to_earliest_branchable_entry() -> None:
    entries = entries_from_json_lines(
        [
            json.dumps({"type": "session_info", "id": "info", "timestamp": 1}),
            json.dumps(
                {
                    "type": "message",
                    "id": "first-message",
                    "parent_id": "info",
                    "timestamp": 2,
                    "message": {"role": "user", "content": "hello"},
                }
            ),
            json.dumps(
                {
                    "type": "label",
                    "id": "legacy-label",
                    "parent_id": "first-message",
                    "timestamp": 3,
                    "label": "Legacy name",
                }
            ),
        ]
    )

    label = entries[-1]
    assert isinstance(label, LabelEntry)
    assert label.target_id == "first-message"
    assert SessionState.from_entries(entries).labels_by_id == {"first-message": "Legacy name"}


def test_compaction_entry_omits_empty_legacy_id_list_from_jsonl() -> None:
    line = entry_to_json_line(
        CompactionEntry(
            id="compact",
            summary="summary",
            first_kept_entry_id="kept",
            tokens_before=42,
        )
    )

    assert json.loads(line) == {
        "id": "compact",
        "timestamp": json.loads(line)["timestamp"],
        "type": "compaction",
        "summary": "summary",
        "first_kept_entry_id": "kept",
        "tokens_before": 42,
    }
    assert entry_from_json_line(line).replaces_entry_ids == []


def test_legacy_summary_entries_load_without_usage() -> None:
    compaction = entry_from_json_line(
        '{"type":"compaction","id":"compact","summary":"old","replaces_entry_ids":[]}'
    )
    branch = entry_from_json_line('{"type":"branch_summary","id":"branch","summary":"old"}')

    assert isinstance(compaction, CompactionEntry)
    assert compaction.usage is None
    assert compaction.provider is None
    assert compaction.model is None
    assert compaction.response_provider is None
    assert isinstance(branch, BranchSummaryEntry)
    assert branch.usage is None
    assert branch.provider is None
    assert branch.model is None
    assert branch.response_provider is None


def test_sum_usage_combines_tokens_optional_fields_and_cost() -> None:
    combined = sum_usage(
        [
            Usage(input=10, reasoning=2, cost=UsageCost(input=0.1, total=0.1)),
            Usage(
                input=20,
                output=5,
                cache_write_1h=3,
                cost=UsageCost(output=0.2, total=0.2),
            ),
        ]
    )

    assert combined.input == 30
    assert combined.output == 5
    assert combined.reasoning == 2
    assert combined.cache_write_1h == 3
    assert combined.cost.input == 0.1
    assert combined.cost.output == 0.2
    assert combined.cost.total == pytest.approx(0.3)


def test_summary_entry_usage_round_trips_with_camel_case_usage_fields() -> None:
    entry = CompactionEntry(
        id="compact",
        summary="summary",
        first_kept_entry_id="kept",
        usage=Usage(input=10, cache_read=20, cache_write_1h=5),
        provider="anthropic",
        model="claude-test",
        response_provider="inference-route",
    )

    line = entry_to_json_line(entry)

    assert '"cacheRead":20' in line
    assert '"cacheWrite1H":5' in line
    assert '"response_provider":"inference-route"' in line
    assert entry_from_json_line(line) == entry


def test_session_state_applies_compaction_and_branch_summary() -> None:
    entries = [
        MessageEntry(id="user", message=UserMessage(content="Explain sessions.")),
        MessageEntry(
            id="assistant",
            parent_id="user",
            message=AssistantMessage(content="They are trees."),
        ),
        CompactionEntry(
            id="compact",
            parent_id="assistant",
            summary="The user asked about sessions.",
            replaces_entry_ids=["user", "assistant"],
        ),
        BranchSummaryEntry(
            id="branch",
            parent_id="compact",
            summary="A side branch explored storage.",
        ),
    ]

    state = SessionState.from_entries(entries)

    assert [message.role for message in state.messages] == ["user", "user"]
    assert "The user asked about sessions." in state.messages[0].text
    assert "A side branch explored storage." in state.messages[1].text


@pytest.mark.parametrize(
    ("boundary", "expected"),
    [
        ("first", ["summary", "first", "middle", "last", "after"]),
        ("middle", ["summary", "middle", "last", "after"]),
        ("last", ["summary", "last", "after"]),
        ("missing", ["summary", "after"]),
        (None, ["summary", "after"]),
    ],
)
def test_session_state_applies_first_kept_boundary_inclusively(
    boundary: str | None,
    expected: list[str],
) -> None:
    entries = [
        MessageEntry(id="first", message=UserMessage(content="first")),
        MessageEntry(
            id="middle",
            parent_id="first",
            message=AssistantMessage(content="middle"),
        ),
        MessageEntry(id="last", parent_id="middle", message=UserMessage(content="last")),
        CompactionEntry(
            id="compact",
            parent_id="last",
            summary="summary",
            first_kept_entry_id=boundary,
        ),
        MessageEntry(id="after", parent_id="compact", message=UserMessage(content="after")),
    ]

    state = SessionState.from_entries(entries)

    assert [
        message.text.removeprefix("Previous conversation summary:\n") for message in state.messages
    ] == expected
    assert state.context_entry_ids == (
        "compact",
        *(entry_id for entry_id in ("first", "middle", "last", "after") if entry_id in expected),
    )


def test_first_kept_boundary_can_reference_non_message_path_entry() -> None:
    entries = [
        MessageEntry(id="first", message=UserMessage(content="first")),
        ModelChangeEntry(
            id="model",
            parent_id="first",
            model="new-model",
        ),
        MessageEntry(id="kept", parent_id="model", message=UserMessage(content="kept")),
        CompactionEntry(
            id="compact",
            parent_id="kept",
            summary="summary",
            first_kept_entry_id="model",
        ),
        MessageEntry(id="after", parent_id="compact", message=UserMessage(content="after")),
    ]

    state = SessionState.from_entries(entries)

    assert [message.text for message in state.messages] == [
        "Previous conversation summary:\nsummary",
        "kept",
        "after",
    ]
    assert state.context_entry_ids == ("compact", "kept", "after")


def test_first_kept_replay_includes_compaction_after_boundary_in_path_order() -> None:
    entries = [
        MessageEntry(id="first", message=UserMessage(content="first")),
        MessageEntry(id="kept", parent_id="first", message=UserMessage(content="kept")),
        CompactionEntry(
            id="old-compact",
            parent_id="kept",
            summary="old summary",
            first_kept_entry_id="kept",
        ),
        MessageEntry(
            id="after-old",
            parent_id="old-compact",
            message=UserMessage(content="after old"),
        ),
        CompactionEntry(
            id="new-compact",
            parent_id="after-old",
            summary="new summary",
            first_kept_entry_id="kept",
        ),
    ]

    state = SessionState.from_entries(entries)

    assert [message.text for message in state.messages] == [
        "Previous conversation summary:\nnew summary",
        "kept",
        "Previous conversation summary:\nold summary",
        "after old",
    ]
    assert state.context_entry_ids == (
        "new-compact",
        "kept",
        "old-compact",
        "after-old",
    )


def test_session_state_applies_first_kept_boundary_on_active_branch_only() -> None:
    entries = [
        MessageEntry(id="root", message=UserMessage(content="root")),
        MessageEntry(
            id="left",
            parent_id="root",
            message=AssistantMessage(content="abandoned left"),
        ),
        MessageEntry(
            id="right",
            parent_id="root",
            message=AssistantMessage(content="kept right"),
        ),
        CompactionEntry(
            id="compact",
            parent_id="right",
            summary="right summary",
            first_kept_entry_id="right",
        ),
        MessageEntry(id="after", parent_id="compact", message=UserMessage(content="after")),
    ]

    state = SessionState.from_entries(entries)

    assert [message.text for message in state.messages] == [
        "Previous conversation summary:\nright summary",
        "kept right",
        "after",
    ]
    assert state.context_entry_ids == ("compact", "right", "after")


def test_legacy_compaction_with_explicit_empty_id_list_keeps_prior_context() -> None:
    legacy_compaction = entry_from_json_line(
        json.dumps(
            {
                "type": "compaction",
                "id": "compact",
                "parent_id": "first",
                "timestamp": 2,
                "summary": "legacy summary",
                "replaces_entry_ids": [],
            }
        )
    )
    entries = [
        MessageEntry(id="first", message=UserMessage(content="first")),
        legacy_compaction,
    ]

    state = SessionState.from_entries(entries)

    assert [message.text for message in state.messages] == [
        "first",
        "Previous conversation summary:\nlegacy summary",
    ]
    assert state.context_entry_ids == ("first", "compact")


async def test_legacy_compaction_fixture_replays_with_id_list_precedence(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "legacy_compaction.jsonl"
    session_path = tmp_path / "legacy_compaction.jsonl"
    session_path.write_bytes(fixture.read_bytes())
    entries = await JsonlSessionStorage(session_path).read_all()

    compaction = next(entry for entry in entries if entry.type == "compaction")
    state = SessionState.from_entries(entries)

    assert compaction.replaces_entry_ids == ["root", "tail"]
    assert compaction.first_kept_entry_id == "tail"
    assert [message.text for message in state.messages] == [
        "Previous conversation summary:\nlegacy summary",
        "keep middle",
        "after compaction",
    ]
    assert state.context_entry_ids == ("compact", "middle", "after")


def test_path_to_entry_follows_parent_chain() -> None:
    root = MessageEntry(id="root", message=UserMessage(content="Hi"))
    child = MessageEntry(id="child", parent_id="root", message=AssistantMessage(content="Hello"))
    leaf = LeafEntry(id="leaf", parent_id="child", entry_id="child")

    assert [entry.id for entry in path_to_entry([root, child, leaf], "child")] == [
        "root",
        "child",
    ]


def test_path_to_entry_rejects_missing_or_cyclic_parent() -> None:
    with pytest.raises(SessionTreeError):
        path_to_entry([], "missing")

    first = CustomEntry(id="first", parent_id="second", namespace="x")
    second = CustomEntry(id="second", parent_id="first", namespace="x")
    with pytest.raises(SessionTreeError):
        path_to_entry([first, second], "first")
