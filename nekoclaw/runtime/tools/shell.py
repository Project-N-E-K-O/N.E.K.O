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

"""The command tool: ``pwsh`` on Windows, ``bash`` elsewhere.

Behavior follows dsh ``packages/shell/tool-pwsh`` / ``tool-bash`` and their
local executors: a fresh shell per call, UTF-8 output forced on PowerShell,
pagers disabled, output kept to its tail with the full text saved to a file,
and a command that reaches its timeout moved to the background as a job
instead of being killed.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from nekoclaw.runtime.loop.tools import AgentTool
from nekoclaw.runtime.tools import procs
from nekoclaw.runtime.tools.base import (
    ToolAborted,
    ToolCallInfo,
    ToolContext,
    ToolError,
    define_tool,
)
from nekoclaw.runtime.tools.jobs import KILL_SETTLE_SECONDS, Job, render_delta

# dsh pwsh-local / bash-local defaults.
MAX_OUTPUT_BYTES = 64_000
ENV_OVERRIDES = {"NO_COLOR": "1", "PAGER": "cat", "GIT_PAGER": "cat"}
ENCODING_PREAMBLE = (
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)
# Live progress: how often new output is forwarded and how much of it at once.
_PROGRESS_INTERVAL_SECONDS = 0.3
_PROGRESS_MAX_CHARS = 4000


def shell_kind(platform: str) -> str:
    """Return the command tool offered on ``platform``."""
    return "pwsh" if platform == "win32" else "bash"


def candidate_powershell_paths(env: dict[str, str] | None = None) -> list[str]:
    """PowerShell 7 locations first, Windows PowerShell 5.1 last."""
    env = dict(os.environ) if env is None else env
    program_files = env.get("ProgramFiles") or "C:\\Program Files"
    system_root = env.get("SystemRoot") or "C:\\Windows"
    candidates = [os.path.join(program_files, "PowerShell", "7", "pwsh.exe")]
    for entry in (env.get("PATH") or "").split(";"):
        trimmed = entry.strip().strip('"')
        if trimmed:
            candidates.append(os.path.join(trimmed, "pwsh.exe"))
    candidates.append(
        os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    )
    return candidates


_resolved_powershell: str | None = None


def resolve_powershell() -> str:
    """Return the PowerShell executable to use; resolved once per process."""
    global _resolved_powershell
    if _resolved_powershell is None:
        found = "pwsh"
        if sys.platform == "win32":
            for candidate in candidate_powershell_paths():
                if os.path.isfile(candidate):
                    found = candidate
                    break
        _resolved_powershell = found
    return _resolved_powershell


def shell_argv(kind: str, command: str) -> list[str]:
    if kind == "pwsh":
        return [
            resolve_powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"{ENCODING_PREAMBLE}{command}",
        ]
    bash = shutil.which("bash")
    if bash is None:
        raise ToolError("bash was not found on PATH")
    return [bash, "-c", command]


def _stream_text(buffer: procs.OutputBuffer) -> str:
    text, _next, truncated = buffer.read_from(0)
    if not truncated:
        return text
    where = buffer.spill_path if buffer.spill_path is not None else "(unavailable)"
    return f"{text}\n[output truncated; full output: {where}]"


def render_result(job: Job, *, timed_out_ms: int | None = None) -> str:
    """Render a finished foreground command: output tail, then status markers."""
    process = job.process
    out = _stream_text(process.stdout)
    err = _stream_text(process.stderr)
    body = out
    if err:
        if body and not body.endswith("\n"):
            body += "\n"
        body += f"[stderr]\n{err}"
    if not body:
        body = "(no output)"
    markers: list[str] = []
    if timed_out_ms is not None:
        markers.append(f"[timed out after {timed_out_ms}ms]")
    if job.stop_reason is not None:
        markers.append(f"[stopped: {job.stop_reason}]")
    signal_name = process.signal_name
    if signal_name is not None:
        markers.append(f"[killed by signal: {signal_name}]")
    elif process.returncode != 0:
        markers.append(f"[exit code: {process.returncode}]")
    if not markers:
        return body
    if not body.endswith("\n"):
        body += "\n"
    return body + "\n".join(markers)


def render_promoted(job: Job, output: str, timeout_ms: int) -> str:
    body = (output if output.endswith("\n") else f"{output}\n") if output else ""
    return f"{body}[still running after {timeout_ms}ms; moved to background job {job.id}]"


class _ProgressFeed:
    """Forwards new output of a running command to the tool call, rate limited."""

    def __init__(self, job: Job, call: ToolCallInfo) -> None:
        self._job = job
        self._call = call
        self._out = 0
        self._err = 0
        self._last = 0.0

    def tick(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < _PROGRESS_INTERVAL_SECONDS:
            return
        process = self._job.process
        out, self._out, _lossy_out = process.stdout.read_from(self._out)
        err, self._err, _lossy_err = process.stderr.read_from(self._err)
        if not out and not err:
            return
        self._last = now
        text = render_delta(out, err, False, [])
        self._call.progress(text[-_PROGRESS_MAX_CHARS:])


def build_shell_tool(ctx: ToolContext, schemas: dict[str, dict[str, Any]]) -> list[AgentTool]:
    kind = shell_kind(ctx.platform)
    registry = ctx.jobs

    def resolve_workdir(raw: Any) -> Path:
        if raw is None or not str(raw).strip():
            return ctx.workdir
        path = ctx.resolve(str(raw))
        if not path.is_dir():
            raise ToolError(f'invalid workdir: "{path}" is not a directory')
        return path

    async def run(args: dict[str, Any], call: ToolCallInfo) -> str:
        command = str(args["command"])
        if not command.strip():
            raise ToolError("invalid command: expected a non-empty string")
        if not str(args["description"]).strip():
            raise ToolError("invalid description: expected a non-empty string")
        requested = args.get("timeoutMs")
        if requested is not None and float(requested) <= 0:
            raise ToolError(f"invalid timeoutMs: expected a positive number, got {requested}")
        timeout_ms = int(
            min(
                float(requested) if requested is not None else ctx.limits.command_timeout_ms,
                ctx.limits.command_max_timeout_ms,
            )
        )
        workdir = resolve_workdir(args.get("workdir"))
        background = args.get("run_in_background") is True
        registry.ensure_capacity()
        if call.cancelled():
            raise ToolAborted

        def reserve(stream: str) -> Any:
            return lambda: ctx.spill.reserve(f"{kind}-{stream}", ".log")

        stdout = procs.OutputBuffer(MAX_OUTPUT_BYTES, reserve("stdout"))
        stderr = procs.OutputBuffer(MAX_OUTPUT_BYTES, reserve("stderr"))
        try:
            process = await procs.spawn(
                shell_argv(kind, command),
                cwd=str(workdir),
                env={**os.environ, **ENV_OVERRIDES},
                stdout=stdout,
                stderr=stderr,
            )
        except FileNotFoundError as exc:
            raise ToolError(f"{kind} could not be started: {exc}") from exc
        except OSError as exc:
            raise ToolError(f"{kind} could not be started: {exc}") from exc

        async def stop_aborted(job: Job) -> None:
            job.notice_suppressed = True
            registry.kill(job, "tool call aborted")
            await registry.wait(job, KILL_SETTLE_SECONDS, _detached(call))
            registry.remove(job)

        job = registry.start(kind, command, process, foreground=not background)
        if call.cancelled():
            # The run was aborted while the process was being created. The abort
            # stopped the jobs it could see; this one did not exist yet.
            await stop_aborted(job)
            raise ToolAborted
        if background:
            return f"started background job {job.id}"

        progress = _ProgressFeed(job, call)
        outcome = await registry.wait(job, timeout_ms / 1000, call, on_tick=progress.tick)
        if outcome == "aborted":
            await stop_aborted(job)
            raise ToolAborted
        if outcome == "timeout":
            # The command keeps running as the job it already was.
            job.foreground = False
            return render_promoted(job, registry.read(job), timeout_ms)
        progress.tick(force=True)
        registry.remove(job)
        return render_result(job)

    return [define_tool(schemas[kind], run)]


def _detached(call: ToolCallInfo) -> ToolCallInfo:
    """Return the call without its cancellation token, for waits that must finish."""
    return ToolCallInfo(id=call.id, name=call.name, signal=None, on_update=None)
