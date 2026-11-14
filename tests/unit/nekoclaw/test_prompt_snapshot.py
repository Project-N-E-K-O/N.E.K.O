"""Locks the system prompt and the tool definitions a task agent's model receives.

The wording comes from dsh, where it was tuned against benchmarks, so a change
must be deliberate. When one is intended, regenerate the expected files with::

    NEKOCLAW_UPDATE_SNAPSHOTS=1 pytest tests/unit/nekoclaw/test_prompt_snapshot.py

and review the diff of ``tests/unit/nekoclaw/snapshots/``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from config.prompts import prompts_nekoclaw as texts
from config.prompts.prompts_nekoclaw_tools import DSH_TOOL_SCHEMAS, NEKOCLAW_TOOL_SCHEMAS
from nekoclaw.runtime.host import HOST_CALLS
from nekoclaw.runtime.prompt import build_system_prompt
from nekoclaw.runtime.protocol import RuntimeLimits
from nekoclaw.runtime.tools import PARALLEL_TOOLS, build_tools
from nekoclaw.runtime.tools.base import SessionInbox, SpillStore, ToolContext
from nekoclaw.runtime.tools.goal import GoalState
from nekoclaw.runtime.tools.jobs import JobRegistry
from nekoclaw.runtime.tools.todo import TodoState
from utils.llm_prompt_leak_check import check_text_for_leaks

SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATE = os.environ.get("NEKOCLAW_UPDATE_SNAPSHOTS") == "1"


class _Host:
    def __init__(self, capabilities: frozenset[str]) -> None:
        self.capabilities = capabilities

    async def call(self, session_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("no host call is made while building tools")


def _build(platform: str, workdir: str, capabilities: frozenset[str], *, images: bool, **prompt: Any):
    context = ToolContext(
        session_id="snapshot",
        workdir=Path(workdir),
        spill=SpillStore(Path(workdir) / "outputs"),
        host=_Host(capabilities),
        limits=RuntimeLimits(),
        supports_images=images,
        platform=platform,
        inbox=SessionInbox(),
        jobs=JobRegistry(lambda job: None),
        todos=TodoState(),
        goals=GoalState(default_rounds=20, max_rounds=100),
        emit_report=lambda payload: None,
    )
    tools = build_tools(context)
    system = build_system_prompt(
        tool_names=[tool.name for tool in tools], workdir=workdir, platform=platform, **prompt
    )
    schemas = [
        {"name": tool.name, "description": tool.description, "parameters": dict(tool.parameters)}
        for tool in tools
    ]
    return tools, system, schemas


VARIANTS: dict[str, dict[str, Any]] = {
    # Windows, a model that accepts images, every host callback present.
    "win32-full": {
        "platform": "win32",
        "workdir": "C:\\work\\project",
        "capabilities": frozenset(HOST_CALLS),
        "images": True,
        "reply_language": "Simplified Chinese",
        "instructions": "Project notes supplied by the host go here.",
    },
    # Elsewhere, a text-only model and no host at all.
    "posix-minimal": {
        "platform": "linux",
        "workdir": "/work/project",
        "capabilities": frozenset(),
        "images": False,
    },
}


def _check(path: Path, actual: str) -> None:
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(actual.encode("utf-8"))
        return
    assert path.is_file(), f"missing snapshot {path.name}; see this module's docstring"
    expected = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert actual == expected, (
        f"{path.name} no longer matches what the runtime sends. If the change is intended, "
        "regenerate the snapshots as described in this module's docstring."
    )


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_system_prompt_and_tool_definitions_match_the_snapshot(name: str) -> None:
    variant = dict(VARIANTS[name])
    _tools, system, schemas = _build(
        variant.pop("platform"), variant.pop("workdir"), variant.pop("capabilities"), **variant
    )
    _check(SNAPSHOTS / f"system-prompt.{name}.expected.md", system + "\n")
    _check(
        SNAPSHOTS / f"tool-schemas.{name}.expected.json",
        json.dumps(schemas, ensure_ascii=False, indent=1) + "\n",
    )


def test_snapshots_are_not_left_in_update_mode() -> None:
    assert not UPDATE, "NEKOCLAW_UPDATE_SNAPSHOTS rewrote the snapshots; rerun without it"


def test_offered_tools_depend_on_platform_images_and_host() -> None:
    full, system_full, _ = _build("win32", "C:\\w", frozenset(HOST_CALLS), images=True)
    minimal, system_minimal, _ = _build("linux", "/w", frozenset(), images=False)
    full_names = [tool.name for tool in full]
    minimal_names = [tool.name for tool in minimal]
    assert full_names == sorted(full_names) and minimal_names == sorted(minimal_names)
    assert minimal_names == [
        "bash", "create_goal", "edit", "get_goal", "glob", "grep", "job_kill", "job_list",
        "job_output", "read", "todo_write", "update_goal", "web_fetch", "write",
    ]
    assert set(full_names) - set(minimal_names) == {
        "pwsh", "read_image", "ask_user_question", "report", "note_lesson", "web_search",
        "subagent", "list_agents", "send_message", "interrupt_agent", "wait_agent",
    }
    assert "bash" not in full_names
    # Only read-only tools may run side by side.
    for tool in full + minimal:
        assert (tool.execution_mode == "parallel") == (tool.name in PARALLEL_TOOLS)
    # A paragraph appears only with its tool.
    assert "web_search results are external" in system_full
    assert "web_search" not in system_minimal
    assert "Check the [exit code: N] marker on every bash result" in system_minimal
    assert "bash result" not in system_full
    assert "`[exit code: 1]` without a signal marker" in system_full
    assert "Shell tool: pwsh" in system_full and "Shell tool: bash" in system_minimal


def test_subagent_tools_need_all_four_host_callbacks() -> None:
    partial = frozenset({"subagent_start", "subagent_list"})
    names = [tool.name for tool in _build("linux", "/w", partial, images=False)[0]]
    assert not {"subagent", "list_agents", "send_message", "wait_agent"} & set(names)


def test_tool_definitions_are_exactly_those_in_config() -> None:
    tools, _system, schemas = _build("win32", "C:\\w", frozenset(HOST_CALLS), images=True)
    catalog = {**DSH_TOOL_SCHEMAS, **NEKOCLAW_TOOL_SCHEMAS}
    for schema in schemas:
        assert schema == catalog[schema["name"]]
    # Nothing in config is left without an implementation (bash is the POSIX twin of pwsh).
    assert set(catalog) - {tool.name for tool in tools} == {"bash"}
    blob = json.dumps(catalog)
    assert "sandbox" not in blob.lower()
    assert "DSH_" not in blob
    assert "ripgrep" not in blob


def test_the_prompt_has_no_unrendered_placeholders_and_no_banned_words() -> None:
    for variant in VARIANTS.values():
        options = dict(variant)
        _tools, system, schemas = _build(
            options.pop("platform"), options.pop("workdir"), options.pop("capabilities"), **options
        )
        assert check_text_for_leaks(system) == []
        assert "<<" not in system
        text = (system + json.dumps(schemas)).lower()
        for banned in ("skill", "master", "deepseek"):
            assert banned not in text


def test_the_desktop_and_browser_paragraphs_match_the_snapshot_and_appear_only_with_their_tools() -> None:
    # The keyboard-and-mouse tool and the browser tools are not tools of the
    # runtime: the host's gateway offers them, by these names.
    from config.prompts.prompts_nekoclaw_builtin import (
        BROWSER_SNAPSHOT_TOOL_NAME,
        BROWSER_TOOL_NAMES,
        COMPUTER_TOOL_NAME,
    )

    variant = dict(VARIANTS["win32-full"])
    platform, workdir = variant.pop("platform"), variant.pop("workdir")
    tools, without, _schemas = _build(platform, workdir, variant.pop("capabilities"), **variant)
    own = [tool.name for tool in tools]
    del variant["images"]

    def prompt(*offered_by_the_gateway: str) -> str:
        return build_system_prompt(
            tool_names=[*own, *offered_by_the_gateway], workdir=workdir, platform=platform, **variant
        )

    with_both = prompt(COMPUTER_TOOL_NAME, *BROWSER_TOOL_NAMES)
    _check(SNAPSHOTS / "system-prompt.win32-builtin-tools.expected.md", with_both + "\n")

    paragraphs = {name: text for name, _triggers, text in texts.TOOL_GUIDANCE_SECTIONS}
    computer, browser = "\n\n" + paragraphs["computer"], "\n\n" + paragraphs["browser"]
    assert prompt() == without
    # Two paragraphs more than without the tools, each there once and nothing else changed.
    assert with_both.count(computer) == 1 and with_both.count(browser) == 1
    assert with_both.replace(computer, "").replace(browser, "") == without
    # Each comes with its own tool, whatever else is offered.
    assert prompt(COMPUTER_TOOL_NAME) == with_both.replace(browser, "")
    assert prompt(BROWSER_SNAPSHOT_TOOL_NAME) == with_both.replace(computer, "")
    assert prompt(*BROWSER_TOOL_NAMES) == with_both.replace(computer, "")
    # Only the tool a paragraph is tied to brings it: other browser tools on
    # their own do not, nor do names that merely look alike.
    for name in ("browser_take_screenshot", "browser_click", "browser", "computer_use", "plugin__computer__run"):
        assert prompt(name) == without
    # Without a single tool of the runtime's own they still follow theirs.
    bare = build_system_prompt(tool_names=[COMPUTER_TOOL_NAME, BROWSER_SNAPSHOT_TOOL_NAME], workdir="/w", platform="linux")
    assert computer in bare and browser in bare


def test_dsh_paragraph_variants_follow_the_offered_tools() -> None:
    def prompt(*names: str) -> str:
        return build_system_prompt(tool_names=names, workdir="/w", platform="linux")

    assert "Read an existing file before overwriting it with write." in prompt("write")
    assert "and prefer edit for targeted changes." in prompt("write", "edit")
    assert prompt("grep").endswith("to search file contents.")
    assert "Use read on a matched file" in prompt("grep", "read")
    assert "Use the returned source snippets when available" in prompt("web_search")
    assert "Follow up with web_fetch" in prompt("web_search", "web_fetch")
    assert texts.TASK_AGENT_IDENTITY in prompt()
