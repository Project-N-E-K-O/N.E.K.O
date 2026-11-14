"""Locate or prepare the interpreter that runs plugin CLI commands.

Source checkouts run ``neko-plugin`` as ``[sys.executable, -m
plugin.neko_plugin_cli.cli, ...]`` with the repository as the working
directory. A packaged backend cannot do that: the Nuitka binary has no
``-m`` support and the interpreter it embeds cannot run pytest. Instead a
packaged build stages one per-user Python environment (``uv venv`` plus
the wheel and hashed requirements bundled next to the executable) under
the plugin state root's sibling ``.neko-plugin-tools`` directory, and
every workbench feature — create-repo, precheck, publish — runs the same
upstream CLI commands through it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import portalocker

from plugin import settings
from plugin.logging_config import get_logger
from plugin.neko_plugin_cli.tool_resources import plugin_tool_resources_dir
from plugin.server.application.plugin_project_commands import (
    cli_child_env,
    run_cli,
)
from utils.file_utils import atomic_write_text

logger = get_logger("server.application.plugin_tool_environment")


class ToolEnvironmentError(RuntimeError):
    pass


ToolEnvironmentState = Literal[
    "source", "ready", "preparing", "missing", "failed", "unavailable"
]


@dataclass(frozen=True)
class ToolEnvironment:
    python: Path
    cwd: Path
    env: dict[str, str]

    def command(self, *args: str) -> list[str]:
        return [str(self.python), "-m", "plugin.neko_plugin_cli.cli", *args]


@dataclass(frozen=True)
class ToolResources:
    root: Path
    key: str
    python: str
    wheel: str
    hashes: dict[str, str]

    @property
    def environment_root(self) -> Path:
        return settings.get_plugin_state_root().parent / ".neko-plugin-tools" / self.key

    @property
    def executable(self) -> Path:
        return self.environment_root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    @property
    def ready_file(self) -> Path:
        return self.environment_root / ".ready"

    def is_ready(self) -> bool:
        try:
            return self.executable.is_file() and self.ready_file.read_text(encoding="utf-8") == self.key
        except OSError:
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
        raise ToolEnvironmentError("This N.E.K.O. package has no compatible plugin tooling resources") from exc
    return ToolResources(root, hashlib.sha256(contents).hexdigest(), python, wheel, hashes)


def verify_tool_resources(resources: ToolResources) -> None:
    for name, expected in resources.hashes.items():
        try:
            with (resources.root / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == expected:
                    continue
        except OSError as exc:
            raise ToolEnvironmentError(f"Cannot read plugin tooling resource {name}") from exc
        raise ToolEnvironmentError(f"Plugin tooling resource {name} failed its integrity check")


async def _prepare(resources: ToolResources, env: dict[str, str]) -> None:
    uv = shutil.which("uv")
    if uv is None or shutil.which("uvx") is None:
        raise ToolEnvironmentError("Install uv and uvx to prepare the plugin tool environment")
    await asyncio.to_thread(verify_tool_resources, resources)
    root = resources.environment_root
    # uv must use a version-matched interpreter even on machines with another
    # Python installed. Keep its download and install state outside the bundle.
    bootstrap_env = {key: value for key, value in env.items() if not key.startswith("UV_")}
    bootstrap_env["UV_PYTHON_INSTALL_DIR"] = str(root.parent / "python")
    commands = [
        [uv, "venv", "--no-config", "--no-project", "--managed-python", "--clear",
         "--python", resources.python, str(root)],
        [uv, "pip", "sync", "--no-config", "--require-hashes", "--only-binary", ":all:",
         "--python", str(resources.executable), str(resources.root / "requirements.txt")],
        [uv, "pip", "install", "--no-config", "--no-deps",
         "--python", str(resources.executable), str(resources.root / resources.wheel)],
    ]
    for command in commands:
        result = await run_cli(command, cwd=root.parent, timeout=300, merge_stderr=True, env=bootstrap_env)
        if result.returncode:
            detail = (result.stdout or "").strip().splitlines()
            raise ToolEnvironmentError(f"Could not prepare plugin tools: {' '.join(detail[-3:])[-500:]}")
    atomic_write_text(resources.ready_file, resources.key)


_preparation_task: "asyncio.Task[None] | None" = None
_preparation_error: str | None = None


def _source_root() -> Path | None:
    # Lazy: plugin_projects imports this module for the precheck gate.
    from plugin.server.application.plugin_projects import source_repo_root

    return source_repo_root()


def tool_environment_status() -> tuple[ToolEnvironmentState, str | None]:
    """(state, last preparation error) for the workbench toolchain page."""
    if _source_root() is not None:
        return "source", None
    try:
        resources = tool_resources()
    except ToolEnvironmentError as exc:
        return "unavailable", str(exc)
    if resources.is_ready():
        return "ready", None
    if _preparation_task is not None and not _preparation_task.done():
        return "preparing", None
    if _preparation_error is not None:
        return "failed", _preparation_error
    return "missing", None


def tool_environment() -> ToolEnvironment:
    """The interpreter, working directory and environment for CLI commands."""
    root = _source_root()
    env = cli_child_env()
    if root is not None:
        return ToolEnvironment(Path(sys.executable), root, env)
    resources = tool_resources()
    if not resources.is_ready():
        raise ToolEnvironmentError(
            "The plugin tool environment is not prepared yet; "
            "prepare the plugin tool environment first"
        )
    env["NEKO_PLUGIN_TEST_CONFIG"] = str(resources.root / "pytest.ini")
    state = resources.environment_root / "state"
    # Logging/config initialization in the test runner should use its own data
    # directory, even when the host is using the default user data root.
    env["NEKO_STORAGE_SELECTED_ROOT"] = str(state)
    env["NEKO_STORAGE_ANCHOR_ROOT"] = str(state)
    return ToolEnvironment(resources.executable, resources.environment_root, env)


async def _preparation(resources: ToolResources) -> None:
    root = resources.environment_root
    root.parent.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(root.with_suffix(".lock"), timeout=0)
    # Acquiring without a blocking wait keeps cancellation responsive.
    async with asyncio.timeout(600):
        while not resources.is_ready():
            try:
                lock.acquire()
            except portalocker.exceptions.LockException:
                await asyncio.sleep(0.25)
                continue
            try:
                if not resources.is_ready():
                    await _prepare(resources, cli_child_env())
            finally:
                lock.release()


async def _preparation_runner() -> None:
    global _preparation_error
    try:
        await _preparation(tool_resources())
    except (OSError, subprocess.TimeoutExpired, TimeoutError, ToolEnvironmentError) as exc:
        _preparation_error = str(exc)
    except Exception as exc:
        logger.exception("plugin tool environment preparation failed unexpectedly")
        _preparation_error = str(exc)
    else:
        _preparation_error = None


def start_tool_environment_preparation() -> tuple[ToolEnvironmentState, str | None]:
    """Start one background preparation when the packaged environment is missing."""
    global _preparation_task
    state, _ = tool_environment_status()
    if state in ("missing", "failed") and (
        _preparation_task is None or _preparation_task.done()
    ):
        _preparation_task = asyncio.create_task(_preparation_runner())
    return tool_environment_status()
