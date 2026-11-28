"""Locate or prepare the interpreter that runs plugin CLI commands.

Source checkouts run ``neko-plugin`` as ``[sys.executable, -m
plugin.neko_plugin_cli.cli, ...]`` with the repository as the working
directory. A packaged backend cannot do that: the Nuitka binary has no
``-m`` support and the interpreter it embeds cannot run pytest. Instead a
packaged build keeps one per-user virtualenv with the CLI wheel and the
hashed requirements bundled next to the executable, and every workbench
feature (create-repo, precheck, publish) runs the same upstream CLI
commands through it.

uv and the CPython the virtualenv is based on come from the shared plugin
tool layer (:mod:`plugin.server.infrastructure.plugin_tool_runtime`): it
downloads, verifies, locks and reports them. This module only adds the
virtualenv on top, under the layer's reserved ``envs/`` directory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from plugin.logging_config import get_logger
from plugin.neko_plugin_cli.core.lint import UV_EXECUTABLE_ENV
from plugin.neko_plugin_cli.tool_resources import plugin_tool_resources_dir
from plugin.server.application.plugin_project_commands import (
    cli_child_env,
    run_cli,
)
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.infrastructure.plugin_tool_runtime import (
    ProgressEvent,
    ToolRuntime,
    ToolRuntimeError,
    prepare_tool_runtime,
    read_tool_runtime_status,
    tool_envs_dir,
    tool_runtime_env,
)
from utils.file_utils import atomic_write_text

logger = get_logger("server.application.plugin_tool_environment")

# Codes of this module; the shared layer's ToolRuntimeError codes pass through.
EnvironmentErrorCode = Literal["resources_unavailable", "environment_failed"]


class ToolEnvironmentError(RuntimeError):
    def __init__(self, message: str, code: EnvironmentErrorCode = "environment_failed") -> None:
        super().__init__(message)
        self.code: EnvironmentErrorCode = code


ToolEnvironmentState = Literal[
    "source", "ready", "preparing", "missing", "failed", "unavailable"
]


@dataclass(frozen=True)
class ToolEnvironment:
    python: Path
    cwd: Path
    env: dict[str, str]
    """The whole child environment: pass it with ``replace_env=True``."""

    def command(self, *args: str) -> list[str]:
        return [str(self.python), "-m", "plugin.neko_plugin_cli.cli", *args]


@dataclass(frozen=True)
class ToolEnvironmentStatus:
    state: ToolEnvironmentState
    error: str | None = None
    # A ToolRuntimeError code or an EnvironmentErrorCode, for localized text.
    error_code: str | None = None
    # While preparing: the shared layer's latest progress event, or this
    # module's own ``creating_environment`` phase.
    tool_progress: dict | None = None
    # read_tool_runtime_status().as_dict() of the shared layer.
    tools: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ToolResources:
    root: Path
    key: str
    python: str
    wheel: str
    hashes: dict[str, str]

    @property
    def environment_root(self) -> Path:
        # A key prefix keeps Windows paths under site-packages short.
        return tool_envs_dir() / f"plugin-cli-{self.key[:16]}"

    @property
    def executable(self) -> Path:
        return self.environment_root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    @property
    def ready_file(self) -> Path:
        return self.environment_root / ".ready"

    def is_ready(self, base_python: str | None) -> bool:
        """Built from these resources on top of ``base_python``. Reads only."""
        try:
            record = json.loads(self.ready_file.read_text(encoding="utf-8"))
            return (
                self.executable.is_file()
                and record.get("key") == self.key
                and base_python is not None
                and record.get("python") == base_python
            )
        except (OSError, ValueError, AttributeError):
            return False


def tool_resources(backend: Path | None = None) -> ToolResources:
    root = plugin_tool_resources_dir(backend or Path(sys.executable))
    try:
        contents = (root / "manifest.json").read_bytes()
        manifest = json.loads(contents)
        wheel = manifest["wheel"]
        python = manifest["python"]
        hashes = manifest["sha256"]
        if (
            manifest["schema"] != 1
            or manifest["platform"] != sys.platform
            or manifest["architecture"] != platform.machine().lower()
            or not re.fullmatch(r"3\.11\.\d+", python)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+\.whl", wheel)
            or set(hashes) != {wheel, "requirements.txt", "pytest.ini"}
            or any(not re.fullmatch(r"[a-f0-9]{64}", digest) for digest in hashes.values())
        ):
            raise ValueError("invalid tooling manifest")
        if any(not (root / name).is_file() for name in hashes):
            raise ValueError("incomplete tooling resources")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ToolEnvironmentError(
            "This N.E.K.O. package has no compatible plugin tooling resources", "resources_unavailable"
        ) from exc
    return ToolResources(root, hashlib.sha256(contents).hexdigest(), python, wheel, hashes)


def verify_tool_resources(resources: ToolResources) -> None:
    for name, expected in resources.hashes.items():
        try:
            with (resources.root / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == expected:
                    continue
        except OSError as exc:
            raise ToolEnvironmentError(
                f"Cannot read plugin tooling resource {name}", "resources_unavailable"
            ) from exc
        raise ToolEnvironmentError(
            f"Plugin tooling resource {name} failed its integrity check", "resources_unavailable"
        )


# ---------------------------------------------------------------------------
# Child environment

# Package index settings a user sets to reach PyPI through a mirror. The tool
# layer drops every inherited UV_* variable; installs into the tool
# environment and the CLI's own dependency sync get these back.
_INSTALL_INDEX_VARS = frozenset({"UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX"})
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@")


def _redact(text: str) -> str:
    # uv output can carry an authenticated index URL.
    return _URL_CREDENTIALS.sub(r"\1***@", text)


def _install_env(runtime_env: dict[str, str]) -> dict[str, str]:
    """The packaged runtime's environment plus the user's index settings."""
    env = dict(runtime_env)
    for name, value in os.environ.items():
        # Case-insensitive, as Windows treats environment names.
        if name.upper() in _INSTALL_INDEX_VARS:
            env[name] = value
    return env


def _packaged_cli_env(resources: ToolResources, uv: str) -> dict[str, str]:
    env = _install_env(tool_runtime_env())
    env["PYTHONUNBUFFERED"] = "1"
    # The prepared uv has no uvx next to it: the CLI's dependency sync finds
    # uv through UV and runs ruff with `uv tool run` through NEKO_PLUGIN_UV.
    env["UV"] = uv
    env[UV_EXECUTABLE_ENV] = uv
    path = env.get("PATH")
    env["PATH"] = str(Path(uv).parent) + (os.pathsep + path if path else "")
    env["NEKO_PLUGIN_TEST_CONFIG"] = str(resources.root / "pytest.ini")
    state = resources.environment_root / "state"
    # Logging/config initialization in the test runner should use its own data
    # directory, even when the host is using the default user data root.
    env["NEKO_STORAGE_SELECTED_ROOT"] = str(state)
    env["NEKO_STORAGE_ANCHOR_ROOT"] = str(state)
    return env


def _source_root() -> Path | None:
    # Lazy: plugin_projects imports this module for the precheck gate.
    from plugin.server.application.plugin_projects import source_repo_root

    return source_repo_root()


# ---------------------------------------------------------------------------
# Status and the environment for CLI commands

_state_lock = threading.Lock()
_preparation_task: "asyncio.Task[None] | None" = None
_preparation_progress: dict | None = None
_preparation_error: tuple[str, str] | None = None  # (detail, code)


def _tools_status() -> tuple[str, str | None, str | None, dict]:
    tools = read_tool_runtime_status()
    status = tools.as_dict()
    if tools.error_detail:
        status["error_detail"] = _redact(tools.error_detail)
    return tools.state, tools.python, tools.uv, status


def tool_environment_status() -> ToolEnvironmentStatus:
    """State of the CLI environment for the workbench. Starts nothing."""
    tools_state, python, _uv, tools = _tools_status()
    if tools_state == "source":
        if _source_root() is None:
            return ToolEnvironmentStatus(
                "unavailable", "This N.E.K.O. installation has no source checkout",
                "resources_unavailable", tools=tools,
            )
        return ToolEnvironmentStatus("source", tools=tools)
    try:
        resources = tool_resources()
    except ToolEnvironmentError as exc:
        return ToolEnvironmentStatus("unavailable", str(exc), exc.code, tools=tools)
    with _state_lock:
        running = _preparation_task is not None and not _preparation_task.done()
        progress = _preparation_progress
        error = _preparation_error
    if tools_state == "ready" and resources.is_ready(python):
        return ToolEnvironmentStatus("ready", tools=tools)
    if running:
        return ToolEnvironmentStatus("preparing", tool_progress=progress, tools=tools)
    if tools_state == "preparing":
        # Another feature (the dependency page, say) is preparing the shared uv / Python.
        return ToolEnvironmentStatus("preparing", tool_progress=tools.get("progress"), tools=tools)
    if error is not None:
        return ToolEnvironmentStatus("failed", error[0], error[1], tools=tools)
    if tools_state == "unsupported":
        # Still retryable: a later attempt can find a uv installed since.
        return ToolEnvironmentStatus(
            "failed", tools.get("error_detail"), tools.get("error_code"), tools=tools,
        )
    return ToolEnvironmentStatus("missing", tools=tools)


def tool_environment() -> ToolEnvironment:
    """The interpreter, working directory and environment for CLI commands.

    Reads only: a packaged build must have been prepared first.
    """
    tools_state, python, uv, _tools = _tools_status()
    if tools_state == "source":
        root = _source_root()
        if root is None:
            raise ToolEnvironmentError(
                "This N.E.K.O. installation has no source checkout", "resources_unavailable"
            )
        return ToolEnvironment(Path(sys.executable), root, cli_child_env())
    resources = tool_resources()
    if tools_state != "ready" or uv is None or not resources.is_ready(python):
        raise ToolEnvironmentError(
            "The plugin tool environment is not prepared yet; "
            "prepare the plugin tool environment first"
        )
    return ToolEnvironment(
        resources.executable, resources.environment_root, _packaged_cli_env(resources, uv)
    )


# ---------------------------------------------------------------------------
# Preparation


def _check(cancel: threading.Event, deadline: float) -> None:
    if cancel.is_set():
        raise ToolRuntimeError("canceled", "Plugin tool preparation was canceled")
    if time.monotonic() >= deadline:
        raise ToolRuntimeError("timeout", "Plugin tool preparation exceeded its time budget")


def _set_progress(progress: dict | None) -> None:
    global _preparation_progress
    with _state_lock:
        _preparation_progress = progress


def _on_tool_progress(event: ProgressEvent) -> None:
    _set_progress({
        "phase": event.phase,
        "downloaded_bytes": event.downloaded_bytes,
        "total_bytes": event.total_bytes,
    })


async def _build(resources: ToolResources, runtime: ToolRuntime, deadline: float) -> None:
    await asyncio.to_thread(verify_tool_resources, resources)
    assert runtime.uv is not None  # always set for a packaged runtime
    root = resources.environment_root
    env = _install_env(runtime.env)
    # No --no-config: a user's uv.toml (an index mirror, say) applies, as it
    # does to the dependency page's installs. The interpreter is an explicit
    # path and every requirement is hash-pinned, so it cannot change either.
    commands = [
        [runtime.uv, "venv", "--no-project", "--clear",
         "--python", runtime.python, str(root)],
        [runtime.uv, "pip", "sync", "--require-hashes", "--only-binary", ":all:",
         "--python", str(resources.executable), str(resources.root / "requirements.txt")],
        [runtime.uv, "pip", "install", "--no-deps",
         "--python", str(resources.executable), str(resources.root / resources.wheel)],
    ]
    for command in commands:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise ToolRuntimeError("timeout", "Plugin tool preparation exceeded its time budget")
        try:
            result = await run_cli(
                command, cwd=root.parent, timeout=timeout, merge_stderr=True,
                env=env, replace_env=True,
            )
        except subprocess.TimeoutExpired:
            raise ToolRuntimeError("timeout", "Plugin tool preparation exceeded its time budget") from None
        if result.returncode:
            detail = (result.stdout or "").strip().splitlines()
            raise ToolEnvironmentError(
                f"Could not prepare plugin tools: {_redact(' '.join(detail[-3:]))[-500:]}"
            )
    # Only after every command succeeded; fsync off the event loop.
    await asyncio.to_thread(
        atomic_write_text, resources.ready_file,
        json.dumps({"key": resources.key, "python": runtime.python}),
    )


async def _prepare_environment(
    resources: ToolResources, runtime: ToolRuntime, cancel: threading.Event, deadline: float,
) -> None:
    """Build the virtualenv unless it is already there.

    A packaged build runs one N.E.K.O. instance, and within it the single
    preparation task (start_tool_environment_preparation) serializes builds,
    so no cross-process lock is taken here.
    """
    if resources.is_ready(runtime.python):
        return
    _check(cancel, deadline)
    resources.environment_root.parent.mkdir(parents=True, exist_ok=True)
    _set_progress({"phase": "creating_environment", "downloaded_bytes": None, "total_bytes": None})
    await _build(resources, runtime, deadline)


async def _preparation_runner(cancel: threading.Event) -> None:
    global _preparation_error
    deadline = time.monotonic() + env_seconds("NEKO_PLUGIN_TOOL_ENVIRONMENT_BUDGET", 1800.0)
    error: tuple[str, str] | None = None
    try:
        resources = tool_resources()
        runtime = await asyncio.to_thread(
            prepare_tool_runtime, cancel=cancel, deadline=deadline, progress=_on_tool_progress,
        )
        await _prepare_environment(resources, runtime, cancel, deadline)
    except asyncio.CancelledError:
        # Server shutdown: stop the worker thread too.
        cancel.set()
        raise
    except ToolRuntimeError as exc:
        error = (_redact(exc.detail), exc.code)
    except ToolEnvironmentError as exc:
        error = (str(exc), exc.code)
    except OSError as exc:
        error = (_redact(str(exc)), "environment_failed")
    except Exception as exc:
        # The type only: the message can carry an authenticated index URL.
        logger.error("plugin tool environment preparation failed unexpectedly: {}", type(exc).__name__)
        error = (_redact(str(exc)), "internal_error")
    finally:
        _set_progress(None)
    with _state_lock:
        _preparation_error = error
    if error is not None:
        logger.warning("plugin tool environment preparation failed: code={}", error[1])


def start_tool_environment_preparation() -> ToolEnvironmentStatus:
    """Start one background preparation when the packaged environment is missing.

    Also while another feature prepares the shared uv / Python: this one
    then waits for it and builds the virtualenv afterwards.
    """
    global _preparation_task
    status = tool_environment_status()
    if status.state in ("missing", "failed", "preparing"):
        with _state_lock:
            idle = _preparation_task is None or _preparation_task.done()
            if idle:
                _preparation_task = asyncio.create_task(_preparation_runner(threading.Event()))
    return tool_environment_status()
