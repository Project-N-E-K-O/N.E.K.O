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

"""Tools carried out by the host: questions, reports, lessons and subagents.

Each tool here is offered only when the host injected the callback it needs
(see ``nekoclaw.runtime.host``). Result texts of the dsh tools follow dsh
``packages/interaction/tool-ask-user`` and ``packages/subagent``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from nekoclaw.runtime.host import offers_subagents
from nekoclaw.runtime.loop.tools import AgentTool, AgentToolResult
from nekoclaw.runtime.tools.base import (
    ToolCallInfo,
    ToolContext,
    ToolError,
    define_tool,
    run_cancellable,
    text_result,
)

WAIT_AGENT_MIN_MS = 10_000
WAIT_AGENT_MAX_MS = 3_600_000
WAIT_AGENT_DEFAULT_MS = 30_000
_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
# report statuses that end the turn: the loop stops when every tool result of
# the batch asks for it, so the tool is meant to be called on its own.
TERMINAL_REPORT_STATUSES = frozenset({"waiting", "ready", "blocked", "failed"})


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _host_note(reply: dict[str, Any]) -> str:
    message = reply.get("message")
    return f" {message}" if isinstance(message, str) and message.strip() else ""


def render_answers(reply: dict[str, Any]) -> str:
    """Render the host's answers as the compact JSON the model receives."""
    answers = []
    for item in reply.get("answers") or []:
        if not isinstance(item, dict):
            continue
        selected = item.get("selected")
        entry: dict[str, Any] = {
            "id": str(item.get("id", "")),
            "selected": [str(label) for label in selected] if isinstance(selected, list) else [],
        }
        custom = item.get("custom")
        if isinstance(custom, str) and custom:
            entry["custom"] = custom
        answers.append(entry)
    return _compact({"answers": answers})


def build_host_tools(ctx: ToolContext, schemas: dict[str, dict[str, Any]]) -> list[AgentTool]:
    host = ctx.host
    capabilities = host.capabilities
    session_id = ctx.session_id
    tools: list[AgentTool] = []

    async def ask_host(name: str, payload: dict[str, Any], call: ToolCallInfo) -> dict[str, Any]:
        return await run_cancellable(host.call(session_id, name, payload), call)

    if "ask_user" in capabilities:

        async def ask_user_question(args: dict[str, Any], call: ToolCallInfo) -> str:
            questions = list(args["questions"])
            if not questions:
                raise ToolError("questions must contain at least one question")
            reply = await ask_host("ask_user", {"questions": questions}, call)
            return render_answers(reply)

        tools.append(define_tool(schemas["ask_user_question"], ask_user_question))

    if "report" in capabilities:

        async def report(args: dict[str, Any], call: ToolCallInfo) -> AgentToolResult:
            status = str(args["status"])
            summary = str(args["summary"]).strip()
            if not summary:
                raise ToolError("summary must be a non-empty string")
            payload = {
                "status": status,
                "summary": summary,
                "details": str(args["details"]) if args.get("details") else None,
                "delivery": str(args["delivery"]) if args.get("delivery") else None,
            }
            reply = await ask_host("report", payload, call)
            ctx.emit_report(payload)
            terminal = status in TERMINAL_REPORT_STATUSES
            text = f"report delivered ({status}).{_host_note(reply)}"
            return text_result(text, terminate=True if terminal else None)

        tools.append(define_tool(schemas["report"], report))

    if "note_lesson" in capabilities:

        async def note_lesson(args: dict[str, Any], call: ToolCallInfo) -> str:
            title = str(args["title"]).strip()
            content = str(args["content"]).strip()
            if not title or not content:
                raise ToolError("title and content must be non-empty strings")
            payload = {
                "title": title,
                "content": content,
                "category": str(args["category"]),
                "scope": str(args.get("scope", "project")),
            }
            reply = await ask_host("note_lesson", payload, call)
            return f"lesson submitted.{_host_note(reply)}"

        tools.append(define_tool(schemas["note_lesson"], note_lesson))

    if offers_subagents(capabilities):

        async def subagent(args: dict[str, Any], call: ToolCallInfo) -> str:
            name = args.get("name")
            if name is not None:
                name = str(name)
                if not _AGENT_NAME_RE.match(name) or name == "lead":
                    raise ToolError(
                        "invalid name: use lower-case letters, digits and hyphens, start with a "
                        'letter, at most 32 characters, and not "lead"'
                    )
            prompt = str(args["prompt"])
            if not prompt.strip():
                raise ToolError("prompt must be a non-empty string")
            reply = await ask_host(
                "subagent_start",
                {
                    "description": str(args["description"]),
                    "prompt": prompt,
                    "name": name,
                    "cwd": str(ctx.resolve(str(args["cwd"]))) if args.get("cwd") else None,
                },
                call,
            )
            started = reply.get("name")
            if not isinstance(started, str) or not started:
                raise ToolError("the host did not return a name for the subagent")
            return f"started subagent {started}"

        async def list_agents(args: dict[str, Any], call: ToolCallInfo) -> str:
            del args
            reply = await ask_host("subagent_list", {}, call)
            rows = []
            for agent in reply.get("agents") or []:
                if not isinstance(agent, dict) or not agent.get("name"):
                    continue
                row = f"{agent['name']} [{agent.get('status', 'unavailable')}]"
                description = agent.get("description")
                if isinstance(description, str) and description:
                    row += f" — {description}"
                rows.append(row)
            return "\n".join(rows) if rows else "(no agents)"

        async def send_message(args: dict[str, Any], call: ToolCallInfo) -> str:
            agent = str(args["agent"])
            message = str(args["message"])
            if not agent.strip() or not message.strip():
                raise ToolError("agent and message must be non-empty strings")
            await ask_host("subagent_send", {"agent": agent, "message": message}, call)
            return f"message delivered to agent {agent}"

        async def interrupt_agent(args: dict[str, Any], call: ToolCallInfo) -> str:
            agent = str(args["agent"])
            if not agent.strip():
                raise ToolError("agent must be a non-empty string")
            await ask_host("subagent_interrupt", {"agent": agent}, call)
            return f"interrupt requested for agent {agent}"

        async def wait_agent(args: dict[str, Any], call: ToolCallInfo) -> str:
            timeout_ms = int(args.get("timeout_ms", WAIT_AGENT_DEFAULT_MS))
            if not WAIT_AGENT_MIN_MS <= timeout_ms <= WAIT_AGENT_MAX_MS:
                raise ToolError(
                    f"timeout_ms must be from {WAIT_AGENT_MIN_MS} through {WAIT_AGENT_MAX_MS}"
                )
            arrived = await run_cancellable(ctx.inbox.wait_for_input(timeout_ms / 1000), call)
            return _compact({"timedOut": not arrived})

        tools.extend(
            [
                define_tool(schemas["subagent"], subagent),
                define_tool(schemas["list_agents"], list_agents),
                define_tool(schemas["send_message"], send_message),
                define_tool(schemas["interrupt_agent"], interrupt_agent),
                define_tool(schemas["wait_agent"], wait_agent),
            ]
        )

    return tools
