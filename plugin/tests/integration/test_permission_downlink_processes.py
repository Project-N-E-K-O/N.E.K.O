"""ctx.permissions in a real plugin process: launch snapshot, pushed updates, restart."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from plugin.core import host as host_module
from plugin.core.host import PluginHost
from plugin.sdk.shared.permissions import PERMISSIONS

pytestmark = pytest.mark.plugin_integration

MEMORY = "plugin_sdk.main_agent.memory:read"
FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugin_test_permissions_fixture.py"

_revision = 0


def snapshot(status: str, *, enforced: bool = True) -> dict:
    global _revision
    _revision += 1
    return {
        "revision": _revision,
        "states": {
            permission: {
                "status": status if permission == MEMORY else "undecided",
                "background": False,
                "enforced": enforced and spec.enforced,
            }
            for permission, spec in PERMISSIONS.items()
        },
    }


@pytest.fixture
def plugin_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda logger: None)
    directory = tmp_path / "perm_fixture"
    directory.mkdir()
    (directory / "__init__.py").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    entry = "plugins.perm_fixture:PermissionsFixturePlugin"
    (directory / "plugin.toml").write_text(
        f"[plugin]\nid='perm_fixture'\nname='perm_fixture'\nentry='{entry}'\n", encoding="utf-8"
    )
    return directory, entry


async def _start(plugin_dir, initial: dict | None) -> PluginHost:
    directory, entry = plugin_dir
    host = PluginHost("perm_fixture", entry, directory / "plugin.toml", source_only=True)
    if initial is not None:
        host.set_initial_permissions(initial)
    await host.start(startup_timeout=10, startup_failure="abort")
    return host


async def _state(host: PluginHost) -> dict:
    return await host.trigger("perm_state", {}, timeout=5)


async def _wait_for(host: PluginHost, predicate) -> dict:
    for _ in range(100):
        state = await _state(host)
        if predicate(state):
            return state
        await asyncio.sleep(0.05)
    raise AssertionError(f"plugin state never matched: {state}")


async def test_launch_snapshot_updates_and_restart(plugin_dir):
    host = await _start(plugin_dir, snapshot("granted"))
    try:
        state = await _state(host)
        assert state["at_startup"] == {"status": "granted", "background": False, "enforced": True}

        await host.push_permissions(snapshot("denied"))
        state = await _wait_for(host, lambda s: s["now"]["status"] == "denied")
        assert state["changes"] == [[MEMORY, "denied", True]]

        # Master switch on: same status, nothing enforced.
        await host.push_permissions(snapshot("denied", enforced=False))
        state = await _wait_for(host, lambda s: s["now"]["enforced"] is False)
        assert [MEMORY, "denied", False] in state["changes"]
        # Every enforced permission reports the change, nothing else does.
        enforced = {p for p, spec in PERMISSIONS.items() if spec.enforced}
        assert {c[0] for c in state["changes"][1:]} == enforced
    finally:
        await host.shutdown(timeout=2)

    # A restarted process starts from the snapshot it is launched with.
    host = await _start(plugin_dir, snapshot("granted"))
    try:
        state = await _state(host)
        assert state["at_startup"]["status"] == "granted" and state["changes"] == []
    finally:
        await host.shutdown(timeout=2)


async def test_without_a_launch_snapshot_enforced_permissions_read_as_denied(plugin_dir):
    host = await _start(plugin_dir, None)
    try:
        state = await _state(host)
        assert state["at_startup"] == {"status": "denied", "background": False, "enforced": True}
        await host.push_permissions(snapshot("granted"))
        await _wait_for(host, lambda s: s["now"]["status"] == "granted")
    finally:
        await host.shutdown(timeout=2)
