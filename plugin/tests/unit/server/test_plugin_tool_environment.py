"""The plugin tool environment seam: source interpreter vs. the prepared venv.

uv and Python come from the shared plugin tool layer, which is faked here;
its own behaviour is covered by tests/unit/test_plugin_tool_runtime*.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from plugin.server.application import plugin_projects
from plugin.server.application import plugin_tool_environment as tools
from plugin.server.application.plugin_tool_environment import (
    ToolEnvironmentError,
    ToolResources,
)
from plugin.server.infrastructure import plugin_tool_runtime as runtime_module
from plugin.server.infrastructure.plugin_tool_runtime import (
    ProgressEvent,
    RuntimeStatus,
    ToolRuntime,
    ToolRuntimeError,
)

pytestmark = pytest.mark.plugin_unit

UV = "/tools/uv/0.13.6/uv"
PYTHON = "/tools/python/cpython-3.11.9/bin/python3"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "_preparation_task", None)
    monkeypatch.setattr(tools, "_preparation_error", None)
    monkeypatch.setattr(tools, "_preparation_progress", None)
    monkeypatch.setattr(tools, "tool_envs_dir", lambda: tmp_path / "tools" / "envs")
    monkeypatch.setattr(
        tools, "tool_runtime_env",
        lambda: {"PATH": "/usr/bin", "UV_PYTHON_INSTALL_DIR": str(tmp_path / "tools" / "python")},
    )
    return tmp_path


def _runtime_status(monkeypatch, state: str, **extra) -> None:
    status = RuntimeStatus(
        state, state != "source", "0.13.6",
        python=PYTHON if state in ("ready", "source") else None,
        uv=UV if state in ("ready", "source") else None,
        **extra,
    )
    monkeypatch.setattr(tools, "read_tool_runtime_status", lambda: status)


def _packaged(monkeypatch, state: str = "missing", **extra) -> None:
    monkeypatch.setattr(plugin_projects, "source_repo_root", lambda: None)
    _runtime_status(monkeypatch, state, **extra)


def _resources(monkeypatch, root: Path) -> ToolResources:
    resources = ToolResources(root, "k" * 64, "3.11.9", "cli-1.0.0-py3-none-any.whl", {})
    monkeypatch.setattr(tools, "tool_resources", lambda: resources)
    monkeypatch.setattr(tools, "verify_tool_resources", lambda resources: None)
    return resources


def _built(resources: ToolResources, python: str = PYTHON) -> None:
    resources.executable.parent.mkdir(parents=True, exist_ok=True)
    resources.executable.write_text("", encoding="utf-8")
    resources.ready_file.write_text(json.dumps({"key": resources.key, "python": python}), encoding="utf-8")


def test_source_mode_runs_the_checkout_interpreter(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_projects, "source_repo_root", lambda: tmp_path)
    _runtime_status(monkeypatch, "source")
    env = {"MARKER": "1"}
    monkeypatch.setattr(tools, "cli_child_env", lambda: dict(env))

    status = tools.tool_environment_status()
    assert status.state == "source"
    assert status.tools["state"] == "source"
    environment = tools.tool_environment()
    assert environment.python == Path(sys.executable)
    assert environment.cwd == tmp_path
    assert environment.env == env
    assert environment.command("precheck")[:3] == [
        str(sys.executable), "-m", "plugin.neko_plugin_cli.cli",
    ]


def test_source_without_a_checkout_is_unavailable(monkeypatch):
    monkeypatch.setattr(plugin_projects, "source_repo_root", lambda: None)
    _runtime_status(monkeypatch, "source")

    assert tools.tool_environment_status().state == "unavailable"
    with pytest.raises(ToolEnvironmentError, match="no source checkout"):
        tools.tool_environment()


def test_packaged_without_valid_resources_is_unavailable(monkeypatch):
    _packaged(monkeypatch, "ready")
    status = tools.tool_environment_status()
    assert status.state == "unavailable"
    assert status.error_code == "resources_unavailable"
    assert "no compatible plugin tooling resources" in status.error
    with pytest.raises(ToolEnvironmentError, match="no compatible plugin tooling"):
        tools.tool_environment()


@pytest.mark.parametrize("runtime_state", ["missing", "ready"])
def test_packaged_not_prepared_is_missing_and_tool_environment_refuses(monkeypatch, tmp_path, runtime_state):
    # Neither the shared uv / Python nor the venv on top of them, or only the former.
    _packaged(monkeypatch, runtime_state)
    _resources(monkeypatch, tmp_path / "resources")

    assert tools.tool_environment_status().state == "missing"
    with pytest.raises(ToolEnvironmentError, match="not prepared yet"):
        tools.tool_environment()


def test_a_venv_built_on_another_python_needs_preparing_again(monkeypatch, tmp_path):
    _packaged(monkeypatch, "ready")
    _built(_resources(monkeypatch, tmp_path / "resources"), python="/old/python")

    assert tools.tool_environment_status().state == "missing"


def test_a_venv_without_its_shared_runtime_is_not_ready(monkeypatch, tmp_path):
    # E.g. the tools root was regenerated after a storage migration.
    _packaged(monkeypatch, "missing")
    _built(_resources(monkeypatch, tmp_path / "resources"))

    assert tools.tool_environment_status().state == "missing"


def test_shared_tools_prepared_elsewhere_read_as_preparing(monkeypatch, tmp_path):
    # E.g. the dependency page downloads uv; preparing here then waits for it.
    progress = {"phase": "downloading_uv", "downloaded_bytes": 1, "total_bytes": 2}
    _packaged(monkeypatch, "preparing", progress=progress)
    _resources(monkeypatch, tmp_path / "resources")

    status = tools.tool_environment_status()
    assert (status.state, status.tool_progress) == ("preparing", progress)


async def test_preparing_joins_a_shared_preparation_started_elsewhere(monkeypatch, tmp_path):
    _packaged(monkeypatch, "preparing")
    _resources(monkeypatch, tmp_path / "resources")
    layer = _FakeLayer(monkeypatch, tmp_path)

    await tools.start_tool_environment_preparation()
    assert tools._preparation_task is not None
    await tools._preparation_task
    assert len(layer.runtime_calls) == 1
    assert tools.tool_environment_status().state == "ready"


def test_packaged_ready_uses_the_venv_with_the_shared_runtime_env(monkeypatch, tmp_path):
    _packaged(monkeypatch, "ready")
    resources = _resources(monkeypatch, tmp_path / "resources")
    _built(resources)
    monkeypatch.setenv("UV_INDEX_URL", "https://mirror.example/simple")
    monkeypatch.setenv("UV_CACHE_DIR", "/elsewhere")

    assert tools.tool_environment_status().state == "ready"
    environment = tools.tool_environment()
    environment_root = tmp_path / "tools" / "envs" / f"plugin-cli-{'k' * 16}"
    assert environment.python == environment_root / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    assert environment.cwd == environment_root
    env = environment.env
    assert env["NEKO_PLUGIN_TEST_CONFIG"] == str(tmp_path / "resources" / "pytest.ini")
    assert env["NEKO_STORAGE_SELECTED_ROOT"] == str(environment_root / "state")
    # The CLI's sync and ruff use the prepared uv; PATH finds it as well.
    assert env["UV"] == UV
    assert env["NEKO_PLUGIN_UV"] == UV
    assert env["PATH"].split(os.pathsep) == [str(Path(UV).parent), "/usr/bin"]
    # The layer's environment, plus only the user's package index settings.
    assert env["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "tools" / "python")
    assert env["UV_INDEX_URL"] == "https://mirror.example/simple"
    assert "UV_CACHE_DIR" not in env


class _FakeLayer:
    """prepare_tool_runtime() and run_cli() of a packaged build."""

    def __init__(self, monkeypatch, tmp_path):
        self.runtime_calls: list[dict] = []
        self.commands: list[tuple[list[str], dict]] = []
        self.runtime_error: Exception | None = None
        self.command_result = 0
        self.command_output = ""
        self.command_timeout = False
        self.before_return: threading.Event | None = None
        self.runtime = ToolRuntime(PYTHON, UV, {"RUNTIME": "1"}, tmp_path / "tools", True)
        self._monkeypatch = monkeypatch
        monkeypatch.setattr(tools, "prepare_tool_runtime", self.prepare)
        monkeypatch.setattr(tools, "run_cli", self.run_cli)

    def prepare(self, *, cancel, deadline, progress):
        self.runtime_calls.append({"cancel": cancel, "deadline": deadline})
        progress(ProgressEvent("downloading_uv", 10, 100))
        if self.before_return is not None:
            assert self.before_return.wait(10)
        if self.runtime_error is not None:
            raise self.runtime_error
        _runtime_status(self._monkeypatch, "ready")
        return self.runtime

    async def run_cli(self, argv, **kwargs):
        self.commands.append((argv, kwargs))
        if self.command_timeout:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if argv[1] == "venv":
            executable = Path(argv[-1]) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(argv, self.command_result, self.command_output, "")


async def test_preparation_builds_the_venv_on_the_shared_runtime(monkeypatch, tmp_path):
    _packaged(monkeypatch, "missing")
    resources = _resources(monkeypatch, tmp_path / "resources")
    layer = _FakeLayer(monkeypatch, tmp_path)
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://mirror.example/simple")

    assert (await tools.start_tool_environment_preparation()).state == "preparing"
    task = tools._preparation_task
    assert (await tools.start_tool_environment_preparation()).state == "preparing"
    assert tools._preparation_task is task  # a second call joins, it does not respawn
    await task

    assert len(layer.runtime_calls) == 1
    assert isinstance(layer.runtime_calls[0]["cancel"], threading.Event)
    assert [argv[:3] for argv, _ in layer.commands] == [
        [UV, "venv", "--no-project"], [UV, "pip", "sync"], [UV, "pip", "install"],
    ]
    venv, _ = layer.commands[0]
    assert venv[venv.index("--python") + 1] == PYTHON
    for _argv, kwargs in layer.commands:
        # The runtime's environment replaces the inherited one; index settings return.
        assert kwargs["replace_env"] is True
        assert kwargs["env"] == {"RUNTIME": "1", "UV_DEFAULT_INDEX": "https://mirror.example/simple"}
    assert json.loads(resources.ready_file.read_text(encoding="utf-8")) == {
        "key": resources.key, "python": PYTHON,
    }
    status = tools.tool_environment_status()
    assert status.state == "ready"
    assert status.tool_progress is None


async def test_preparation_reports_the_shared_layer_progress(monkeypatch, tmp_path):
    _packaged(monkeypatch, "missing")
    _resources(monkeypatch, tmp_path / "resources")
    layer = _FakeLayer(monkeypatch, tmp_path)
    layer.before_return = threading.Event()

    await tools.start_tool_environment_preparation()
    try:
        for _ in range(500):
            status = tools.tool_environment_status()
            if status.tool_progress is not None:
                break
            await asyncio.sleep(0.01)
        assert status.state == "preparing"
        assert status.tool_progress == {"phase": "downloading_uv", "downloaded_bytes": 10, "total_bytes": 100}
    finally:
        layer.before_return.set()
    await tools._preparation_task
    assert tools.tool_environment_status().state == "ready"


async def test_a_shared_layer_failure_is_reported_with_its_code_until_the_next_start(monkeypatch, tmp_path):
    _packaged(monkeypatch, "missing")
    _resources(monkeypatch, tmp_path / "resources")
    layer = _FakeLayer(monkeypatch, tmp_path)
    layer.runtime_error = ToolRuntimeError(
        "download_failed", "GET https://user:secret@mirror.example/uv failed",
    )

    await tools.start_tool_environment_preparation()
    await tools._preparation_task
    status = tools.tool_environment_status()
    assert status.state == "failed"
    assert status.error_code == "download_failed"
    assert "secret" not in status.error
    assert not layer.commands

    layer.runtime_error = None
    assert (await tools.start_tool_environment_preparation()).state == "preparing"
    await tools._preparation_task
    assert tools.tool_environment_status().state == "ready"


async def test_a_failed_venv_command_is_reported_redacted(monkeypatch, tmp_path):
    _packaged(monkeypatch, "missing")
    _resources(monkeypatch, tmp_path / "resources")
    layer = _FakeLayer(monkeypatch, tmp_path)
    layer.command_result = 2
    layer.command_output = "error: https://token@mirror.example/simple returned 403\n"

    await tools.start_tool_environment_preparation()
    await tools._preparation_task
    status = tools.tool_environment_status()
    assert status.state == "failed"
    assert status.error_code == "environment_failed"
    assert "403" in status.error
    assert "token" not in status.error
    assert len(layer.commands) == 1  # stops at the first failing command


async def test_running_out_of_the_budget_is_a_timeout_with_a_reason(monkeypatch, tmp_path):
    _packaged(monkeypatch, "missing")
    _resources(monkeypatch, tmp_path / "resources")
    layer = _FakeLayer(monkeypatch, tmp_path)
    layer.command_timeout = True

    await tools.start_tool_environment_preparation()
    await tools._preparation_task
    status = tools.tool_environment_status()
    assert (status.state, status.error_code) == ("failed", "timeout")
    assert status.error  # never an empty reason
    # Each step gets what is left of one budget shared with uv / Python.
    assert 0 < layer.commands[0][1]["timeout"] <= 1800.0


async def test_an_unexpected_preparation_error_is_reported_as_internal(monkeypatch, tmp_path):
    _packaged(monkeypatch, "missing")
    _resources(monkeypatch, tmp_path / "resources")
    layer = _FakeLayer(monkeypatch, tmp_path)
    layer.runtime_error = RuntimeError("disk went away")

    await tools.start_tool_environment_preparation()
    await tools._preparation_task
    status = tools.tool_environment_status()
    assert (status.state, status.error, status.error_code) == ("failed", "disk went away", "internal_error")


@pytest.mark.parametrize(
    ("tools_state", "code", "detail"),
    [
        ("unsupported", "unsupported_platform", "No uv for this platform"),
        # e.g. the dependency page's own uv download failed
        ("failed", "download_failed", "uv download failed"),
    ],
)
async def test_a_failed_shared_runtime_is_failed_and_retryable(monkeypatch, tmp_path, tools_state, code, detail):
    _packaged(monkeypatch, tools_state, error_code=code, error_detail=detail)
    _resources(monkeypatch, tmp_path / "resources")
    _FakeLayer(monkeypatch, tmp_path)

    status = tools.tool_environment_status()
    assert (status.state, status.error, status.error_code) == ("failed", detail, code)
    assert (await tools.start_tool_environment_preparation()).state == "preparing"
    await tools._preparation_task
    assert tools.tool_environment_status().state == "ready"


def test_tampered_resource_hashes_fail_verification(tmp_path, monkeypatch):
    import hashlib

    root = tmp_path / "resources"
    root.mkdir()
    wheel = root / "cli-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    requirements = root / "requirements.txt"
    requirements.write_bytes(b"pytest==0\n")
    test_config = root / "pytest.ini"
    test_config.write_bytes(b"[pytest]\n")
    manifest = {
        "schema": 1,
        "platform": sys.platform,
        "architecture": tools.host_architecture(),
        "python": "3.11.9",
        "wheel": wheel.name,
        "sha256": {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in (
                (wheel.name, wheel),
                ("requirements.txt", requirements),
                ("pytest.ini", test_config),
            )
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(
        tools, "plugin_tool_resources_dir", lambda backend=None: root
    )
    resources = tools.tool_resources()
    tools.verify_tool_resources(resources)

    requirements.write_bytes(b"pytest==1\n")
    with pytest.raises(ToolEnvironmentError, match="integrity check"):
        tools.verify_tool_resources(resources)


def test_resource_architecture_follows_the_interpreter_not_the_machine(tmp_path, monkeypatch):
    # An x64 package emulated on Windows on ARM: platform.machine() can report the
    # native CPU while the interpreter, and the shared tool layer, run as x86_64.
    import hashlib
    import platform

    root = tmp_path / "resources"
    root.mkdir()
    files = {
        "cli-1.0.0-py3-none-any.whl": b"wheel",
        "requirements.txt": b"pytest==0\n",
        "pytest.ini": b"[pytest]\n",
    }
    for name, content in files.items():
        (root / name).write_bytes(content)
    manifest = {
        "schema": 1,
        "platform": sys.platform,
        "architecture": "x86_64",
        "python": "3.11.9",
        "wheel": "cli-1.0.0-py3-none-any.whl",
        "sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(tools, "plugin_tool_resources_dir", lambda backend=None: root)
    monkeypatch.setattr(platform, "machine", lambda: "ARM64")
    monkeypatch.setattr(runtime_module.sysconfig, "get_platform", lambda: "win-amd64")

    assert runtime_module.host_architecture() == "x86_64"
    assert tools.tool_resources().wheel == "cli-1.0.0-py3-none-any.whl"

    monkeypatch.setattr(runtime_module.sysconfig, "get_platform", lambda: "win-arm64")
    with pytest.raises(ToolEnvironmentError) as raised:
        tools.tool_resources()
    assert raised.value.code == "resources_unavailable"
