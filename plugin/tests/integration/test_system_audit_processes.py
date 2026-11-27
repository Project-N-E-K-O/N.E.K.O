"""The system.* audit hook in a real plugin process: records reach the host's
call-chain audit, including the ones still queued when the plugin stops."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from plugin.core import host as host_module
from plugin.core.host import PluginHost
from plugin.sdk.shared.permissions import PERMISSIONS
from plugin.server.application.plugins import call_audit, capability_gate

pytestmark = pytest.mark.plugin_integration

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugin_test_system_audit_fixture.py"


@pytest.fixture
def plugin_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda logger: None)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    directory = tmp_path / "audit_fixture"
    directory.mkdir()
    (directory / "__init__.py").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    entry = "plugins.audit_fixture:SystemAuditFixturePlugin"
    (directory / "plugin.toml").write_text(
        f"[plugin]\nid='audit_fixture'\nname='audit_fixture'\nentry='{entry}'\n", encoding="utf-8"
    )
    return directory, entry


@pytest.fixture
def written(monkeypatch):
    entries = []
    monkeypatch.setattr(call_audit, "record", entries.append)
    monkeypatch.setattr(capability_gate, "_system_use_budget", {})
    return entries


async def test_plugin_uses_are_recorded_and_flushed_at_shutdown(plugin_dir, written, tmp_path):
    directory, entry = plugin_dir
    host = PluginHost("audit_fixture", entry, directory / "plugin.toml", source_only=True)
    host.set_initial_permissions({
        "revision": 1,
        "states": {
            permission: {
                "status": "granted" if permission == "system.network:connect" else "undecided",
                "background": False, "enforced": spec.enforced,
            }
            for permission, spec in PERMISSIONS.items()
        },
    })
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    outside = tmp_path / "outside"
    outside.mkdir()
    await host.start(startup_timeout=10, startup_failure="abort")
    try:
        result = await host.trigger(
            "touch", {"path": str(outside / "report.txt"), "port": server.getsockname()[1]}, timeout=10,
        )
        assert result == {"ok": True}
    finally:
        # Right after the use: the records are still queued in the plugin
        # (the sender wakes once a second) and must be sent on the way out.
        await host.shutdown(timeout=5)
        server.close()
    uses = [e for e in written if e.get("kind") == "system_use" and e["plugin"] == "audit_fixture"]
    by_permission = {e["capability"]: e for e in uses}
    write = by_permission["system.fs:write"]
    assert write["target"].endswith("/outside/*.txt") and write["verdict"] == "would_deny"
    assert write["enforcing"] is False and "plugin_reported" in write["flags"]
    connect = by_permission["system.network:connect"]
    assert connect["verdict"] == "allow" and connect["target"].startswith("127.0.0.1:")
    assert not any("own" in e["target"] for e in uses)
