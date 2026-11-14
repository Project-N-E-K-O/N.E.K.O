"""The plugin tool environment seam: source interpreter vs. the prepared venv."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from plugin.server.application import plugin_projects
from plugin.server.application import plugin_tool_environment as tools
from plugin.server.application.plugin_tool_environment import (
    ToolEnvironmentError,
    ToolResources,
)

pytestmark = pytest.mark.plugin_unit


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "_preparation_task", None)
    monkeypatch.setattr(tools, "_preparation_error", None)
    monkeypatch.setattr(
        tools.settings, "get_plugin_state_root", lambda: tmp_path / "plugins"
    )
    return tmp_path


def _packaged(monkeypatch):
    monkeypatch.setattr(plugin_projects, "source_repo_root", lambda: None)


def _resources(monkeypatch, root: Path, *, ready: bool = False) -> ToolResources:
    resources = ToolResources(root, "key", "3.11.9", "cli-1.0.0-py3-none-any.whl", {})
    monkeypatch.setattr(tools, "tool_resources", lambda: resources)
    if ready:
        monkeypatch.setattr(ToolResources, "is_ready", lambda self: True)
    return resources


def test_source_mode_runs_the_checkout_interpreter(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_projects, "source_repo_root", lambda: tmp_path)
    env = {"MARKER": "1"}
    monkeypatch.setattr(tools, "cli_child_env", lambda: dict(env))

    assert tools.tool_environment_status() == ("source", None)
    environment = tools.tool_environment()
    assert environment.python == Path(sys.executable)
    assert environment.cwd == tmp_path
    assert environment.env == env
    assert environment.command("precheck")[:3] == [
        str(sys.executable), "-m", "plugin.neko_plugin_cli.cli",
    ]


def test_packaged_without_valid_resources_is_unavailable(monkeypatch):
    _packaged(monkeypatch)
    state, error = tools.tool_environment_status()
    assert state == "unavailable"
    assert "no compatible plugin tooling resources" in error
    with pytest.raises(ToolEnvironmentError, match="no compatible plugin tooling"):
        tools.tool_environment()


def test_packaged_not_prepared_is_missing_and_tool_environment_refuses(monkeypatch, tmp_path):
    _packaged(monkeypatch)
    _resources(monkeypatch, tmp_path / "resources")

    assert tools.tool_environment_status() == ("missing", None)
    with pytest.raises(ToolEnvironmentError, match="not prepared yet"):
        tools.tool_environment()


def test_packaged_ready_uses_the_venv_with_test_config(monkeypatch, tmp_path):
    _packaged(monkeypatch)
    _resources(monkeypatch, tmp_path / "resources", ready=True)
    monkeypatch.setattr(tools, "cli_child_env", lambda: {"BASE": "1"})

    assert tools.tool_environment_status() == ("ready", None)
    environment = tools.tool_environment()
    environment_root = (
        tools.settings.get_plugin_state_root().parent / ".neko-plugin-tools" / "key"
    )
    assert environment.python == environment_root / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    assert environment.cwd == environment_root
    assert environment.env["NEKO_PLUGIN_TEST_CONFIG"] == str(tmp_path / "resources" / "pytest.ini")
    assert environment.env["NEKO_STORAGE_SELECTED_ROOT"] == str(environment_root / "state")
    assert environment.env["BASE"] == "1"


async def test_preparation_runs_once_and_reports_ready(monkeypatch, tmp_path):
    _packaged(monkeypatch)
    _resources(monkeypatch, tmp_path / "resources")
    calls = []

    async def prepare(resources, env):
        calls.append(resources)
        monkeypatch.setattr(ToolResources, "is_ready", lambda self: True)

    monkeypatch.setattr(tools, "_prepare", prepare)
    assert tools.start_tool_environment_preparation() == ("preparing", None)
    task = tools._preparation_task
    assert tools.start_tool_environment_preparation() == ("preparing", None)
    assert tools._preparation_task is task  # a second call joins, it does not respawn
    await task
    assert calls and len(calls) == 1
    assert tools.tool_environment_status() == ("ready", None)


async def test_failed_preparation_is_reported_until_the_next_start(monkeypatch, tmp_path):
    _packaged(monkeypatch)
    _resources(monkeypatch, tmp_path / "resources")

    async def fail(resources, env):
        raise ToolEnvironmentError("uv exploded")

    monkeypatch.setattr(tools, "_prepare", fail)
    tools.start_tool_environment_preparation()
    await tools._preparation_task
    assert tools.tool_environment_status() == ("failed", "uv exploded")

    prepared = asyncio.Event()

    async def ok(resources, env):
        prepared.set()
        monkeypatch.setattr(ToolResources, "is_ready", lambda self: True)

    monkeypatch.setattr(tools, "_prepare", ok)
    tools.start_tool_environment_preparation()
    await tools._preparation_task
    assert prepared.is_set()
    assert tools.tool_environment_status() == ("ready", None)


async def test_an_unexpected_preparation_error_is_reported_as_failed(monkeypatch, tmp_path):
    _packaged(monkeypatch)
    _resources(monkeypatch, tmp_path / "resources")

    async def explode(resources, env):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(tools, "_prepare", explode)
    tools.start_tool_environment_preparation()
    await tools._preparation_task
    assert tools.tool_environment_status() == ("failed", "disk went away")


def test_tampered_resource_hashes_fail_verification(tmp_path, monkeypatch):
    import hashlib
    import json

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
        "architecture": __import__("platform").machine().lower(),
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
