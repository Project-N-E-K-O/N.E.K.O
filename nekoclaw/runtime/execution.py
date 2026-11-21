# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Execution-side adapter for tools registered by this runtime (and the dsh host MCP server)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path

from nekoclaw.runtime.tools.protected_outputs import OUTPUT_SCOPE, OutputScope
from nekoclaw.runtime.host import HostBridge
from nekoclaw.runtime.loop.tools import AgentTool
from nekoclaw.runtime.permissions import classify
from nekoclaw.runtime.tools.base import ToolAborted, ToolCallInfo, ToolError, run_cancellable

JOB_TOOLS = frozenset({"job_list", "job_output", "job_kill"})
EXECUTION_TOOLS = JOB_TOOLS | {
    "read", "write", "edit", "read_image", "glob", "grep", "pwsh", "bash", "web_fetch",
}


@dataclass
class CommandOutput:
    receipt: str
    retained: bool = False


COMMAND_OUTPUT: ContextVar[CommandOutput | None] = ContextVar("command_output", default=None)


def resource_ref(request, arguments) -> str:
    """Audit a scope without storing paths, commands, job labels or URL credentials."""
    if request.path:
        resource = ("write" if request.tool in {"write", "edit"} else "read",
                    os.path.normcase(os.path.realpath(request.path)))
    elif request.command is not None:
        resource = (request.tool, request.cwd, request.command)
    elif request.tool == "web_fetch":
        resource = (request.tool, arguments.get("url"))
    else:
        resource = ("jobs", arguments.get("job_id"))
    return hashlib.sha256(json.dumps(resource, ensure_ascii=True).encode()).hexdigest()


def managed_tool(tool: AgentTool, host: HostBridge, session: str, workdir: Path,
                 readable: tuple[Path, ...] = (), jobs=None, *, spill=None, cap=None) -> AgentTool:
    if tool.name not in EXECUTION_TOOLS or "execution_action" not in host.capabilities:
        return tool

    async def execute(call_id, arguments, signal=None, on_update=None):
        request = classify(tool.name, arguments, workdir, readable=readable)
        call = ToolCallInfo(call_id, tool.name, signal, None)
        origins = []
        if jobs is not None and tool.name in JOB_TOOLS:
            visible = jobs.visible()
            origins = [job.origin for job in visible if job.origin and (
                tool.name == "job_list" or job.id == str(arguments.get("job_id", "")).strip())]
        if spill and request.path and (saved := spill.protected.get(Path(request.path))):
            origins.append(saved.origin)
        ruling = await run_cancellable(host.call(session, "execution_action", {
            **({"origins": origins} if origins else {}),
            "phase": "begin", **request.to_json(),
            "scope_ref": await asyncio.to_thread(resource_ref, request, arguments),
        }), call)
        receipt = ruling.get("receipt")
        if ruling.get("allow") is not True or not isinstance(receipt, str) or not receipt:
            reason = ruling.get("reason") or "not allowed by the user"
            raise ToolError(f"permission denied: {reason}")

        from nekoclaw.runtime.tools.jobs import JOB_HANDOFF, JobHandoff

        handoff = JobHandoff(frozenset(ruling.get("origins", []))) if tool.name in JOB_TOOLS else None
        output = CommandOutput(receipt) if tool.name in {"bash", "pwsh"} else None

        protected = OutputScope(receipt) if spill is not None else None

        async def finish(status):
            try:
                reply = await host.call(session, "execution_action", {
                    "phase": "finish", "receipt": receipt, "status": status,
                    **({"retain_source": True} if (output and output.retained) or (protected and protected.retained) else {}),
                })
            except Exception as exc:
                logging.getLogger(__name__).warning("closing a tool call failed (%s)", type(exc).__name__)
                return False
            return reply.get("allow") is True

        progress = None
        pending = None

        async def publish():
            nonlocal pending
            while pending is not None:
                part, pending = pending, None
                try:
                    allowed = await host.call(session, "execution_action", {"phase": "check", "receipt": receipt})
                except Exception:
                    return
                if allowed.get("allow") is not True or call.cancelled():
                    return
                on_update(part)

        def update(part):
            nonlocal pending, progress
            pending = part
            if progress is None or progress.done():
                progress = asyncio.create_task(publish())

        try:
            if call.cancelled():
                raise ToolAborted()
            binding = COMMAND_OUTPUT.set(output)
            spill_binding = OUTPUT_SCOPE.set(protected)
            job_binding = JOB_HANDOFF.set(handoff)
            try:
                result = await tool.execute(call_id, arguments, signal, update if on_update else None)
                if protected and result.text:
                    from nekoclaw.runtime.loop.messages import TextContent
                    from nekoclaw.runtime.tools.fs import READ_MAX_BYTES

                    if len(result.text.encode("utf-8")) > READ_MAX_BYTES:
                        path = await asyncio.to_thread(spill.save, tool.name, result.text)
                        if path is None:
                            raise ToolError("The action ran. Its full text could not be saved.")
                        notice = f"Use grep with a keyword to search the full result at {path}."
                        blocks = [block for block in result.content if not isinstance(block, TextContent)]
                        result = result.model_copy(update={"content": [TextContent(text=notice), *blocks]})
                if protected and cap:
                    result = await cap(tool.name, call_id, result)
                if progress is not None:
                    await progress
            finally:
                JOB_HANDOFF.reset(job_binding)
                OUTPUT_SCOPE.reset(spill_binding)
                COMMAND_OUTPUT.reset(binding)
                if progress is not None and not progress.done():
                    progress.cancel()
                    await asyncio.gather(progress, return_exceptions=True)
            if call.cancelled():
                raise ToolAborted()
        except (asyncio.CancelledError, ToolAborted):
            await finish("cancelled")
            raise
        except Exception:
            if await finish("failed"):
                raise
        else:
            if await finish("completed"):
                if handoff and handoff.acknowledge:
                    handoff.acknowledge()
                # These registered tools have only a model-content recipient.
                return result if result.details is None else result.model_copy(update={"details": None})
        raise ToolError("The tool result could not be delivered.") from None

    return replace(tool, execute_fn=execute)
