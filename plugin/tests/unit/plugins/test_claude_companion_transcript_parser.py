from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugin.plugins.claude_companion import TranscriptParser

pytestmark = pytest.mark.plugin_unit


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def test_parse_latest_turn_collects_tools_across_multi_record_turn(tmp_path: Path) -> None:
    """F0033: tool_use blocks in earlier assistant records are collected even after final text."""
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "old question"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/old/file.py"}}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "old answer"}]}},
            {"type": "user", "message": {"role": "user", "content": "fix the bug in app.py"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/app.py"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/test_app.py"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "1 passed"}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Done fixing app.py"}],
                },
            },
        ],
    )

    parsed = TranscriptParser.parse_latest_turn(str(transcript))
    assert parsed["user_message"] == "fix the bug in app.py"
    assert parsed["assistant_message"] == "Done fixing app.py"
    assert set(parsed["tools_used"]) == {"Edit", "Bash"}
    assert "/repo/app.py" in parsed["files_touched"]
    assert "tests/test_app.py" in parsed["files_touched"]
    assert "/old/file.py" not in parsed["files_touched"]
    assert parsed["has_significant_action"] is True


def test_parse_latest_turn_handles_null_tool_input_and_tool_only_turn(tmp_path: Path) -> None:
    transcript = tmp_path / "tool_only.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "earlier prompt"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "earlier reply"}]}},
            {"type": "user", "message": {"role": "user", "content": "run checks"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "input": None},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "rm -f tmp/out.log"}},
                    ],
                },
            },
        ],
    )

    parsed = TranscriptParser.parse_latest_turn(str(transcript))
    assert parsed["user_message"] == "run checks"
    assert parsed["assistant_message"] == ""
    assert parsed["tools_used"] == ["Bash", "Bash"]
    assert "tmp/out.log" in parsed["files_touched"]
    assert parsed["has_significant_action"] is True
