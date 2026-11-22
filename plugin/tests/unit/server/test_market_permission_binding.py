"""Grant source binding before a Market replacement.

Grants follow the plugin's install source. Before a Market upgrade or a
Market takeover changes anything, a grant record that has no source yet is
bound to the plugin's current source, so the Market source recorded for the
new package is a source change rather than a first binding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core.plugin_layout import resolve_plugin_layout
from plugin.server.application.plugins import permission_service
from plugin.server.application.plugins.installation_transactions import (
    replace as replacement_transaction,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.plugin_permissions import PluginSource
from plugin.server.routes import market_bridge


pytestmark = pytest.mark.plugin_unit

PLUGIN_ID = "demo"
PERMISSION = "plugin_sdk.main_agent.memory:read"


def _entry(channel: str) -> object:
    return market_bridge.LockEntry(
        root_id="user",
        directory_name=PLUGIN_ID,
        plugin_id=PLUGIN_ID,
        package_id=PLUGIN_ID,
        channel=channel,
        reason="user_requested",
        installed_at="2026-08-31T00:00:00.000000Z",
        updated_at="2026-08-31T00:00:00.000000Z",
        last_seen_at="2026-08-31T00:00:00.000000Z",
    )


async def _replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: list[str]):
    plugin_dir = tmp_path / "plugins" / PLUGIN_ID
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.toml"
    manifest.write_text(f'[plugin]\nid = "{PLUGIN_ID}"\nversion = "1.0.0"\n', encoding="utf-8")
    entry = _entry("market")
    manager = type(
        "Manager",
        (),
        {"find_active_user_entry": lambda self, _plugin_id: entry},
    )()

    async def not_running(_plugin_id: str) -> bool:
        return False

    monkeypatch.setattr(replacement_transaction, "_plugin_is_running", not_running)

    async def install_new() -> dict[str, object]:
        calls.append("install_new")
        record = permission_service.permission_store.read(PLUGIN_ID)
        calls.append(f"source_at_install={record.get('source')}")
        plugin_dir.mkdir()
        manifest.write_text(f'[plugin]\nid = "{PLUGIN_ID}"\nversion = "2.0.0"\n', encoding="utf-8")
        return {"installed": True}

    return await market_bridge._replace_market_plugin_transaction(
        manager=manager,  # type: ignore[arg-type]
        expected_plugin_id=PLUGIN_ID,
        original_entry=entry,
        original_entry_fingerprint=market_bridge._market_entry_fingerprint(entry),
        installed_package_id=PLUGIN_ID,
        plugin_dir=plugin_dir,
        layout=resolve_plugin_layout(
            PLUGIN_ID,
            plugin_dir,
            storage_root=tmp_path / "runtime-data",
        ),
        install_new=install_new,
    ), manifest


@pytest.mark.asyncio
async def test_market_replacement_binds_a_source_less_record_before_replacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source-less grant record is bound to the current source first, so
    the Market source that follows drops the previous code's grants."""
    previous = PluginSource("manual")
    monkeypatch.setattr(
        permission_service,
        "current_source",
        lambda plugin_id: previous if plugin_id == PLUGIN_ID else None,
    )
    permission_service.permission_store.decide(PLUGIN_ID, PERMISSION, "granted", source=None)
    calls: list[str] = []

    result, _manifest = await _replace(tmp_path, monkeypatch, calls)

    assert result.install_result == {"installed": True}
    assert calls == ["install_new", f"source_at_install={previous.to_dict()}"]
    # The plugin starts with its new Market source: a source change.
    record = permission_service.permission_store.sync_source(
        PLUGIN_ID, PluginSource("market", "market-demo")
    )
    assert PERMISSION not in record.get("permissions", {})


@pytest.mark.asyncio
async def test_market_replacement_keeps_grants_of_an_unchanged_market_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrading a Market plugin to a newer release of the same Market id is
    not a source change; its grants stay."""
    current = PluginSource("market", "market-demo")
    monkeypatch.setattr(permission_service, "current_source", lambda _plugin_id: current)
    permission_service.permission_store.decide(PLUGIN_ID, PERMISSION, "granted", source=None)

    await _replace(tmp_path, monkeypatch, [])

    record = permission_service.permission_store.sync_source(PLUGIN_ID, current)
    assert record["permissions"][PERMISSION]["state"] == "granted"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "proceeds"),
    [
        ("PLUGIN_PERMISSIONS_WRITE_FAILED", False),
        ("PLUGIN_PERMISSIONS_READ_FAILED", False),
        ("PLUGIN_PERMISSIONS_CORRUPT", True),
        ("PLUGIN_PERMISSIONS_UNSUPPORTED_VERSION", True),
    ],
)
async def test_market_replacement_stops_unchanged_when_the_binding_cannot_be_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, proceeds: bool
) -> None:
    """A store that cannot be read or saved stops the replacement before
    anything changes. A damaged or newer store holds no usable grant, so it
    does not stop it."""
    monkeypatch.setattr(
        permission_service, "current_source", lambda _plugin_id: PluginSource("manual")
    )

    def cannot_bind(_plugin_id, _source):
        raise ServerDomainError(code, "permission store unavailable", 503)

    monkeypatch.setattr(permission_service.permission_store, "bind_unbound_source", cannot_bind)
    calls: list[str] = []

    if proceeds:
        result, manifest = await _replace(tmp_path, monkeypatch, calls)
        assert result.install_result == {"installed": True}
        assert calls[0] == "install_new"
        assert 'version = "2.0.0"' in manifest.read_text(encoding="utf-8")
        return

    with pytest.raises(market_bridge._TaskError) as raised:
        await _replace(tmp_path, monkeypatch, calls)
    assert raised.value.code == code.lower()
    assert raised.value.http_status == 503
    assert calls == []
    manifest = tmp_path / "plugins" / PLUGIN_ID / "plugin.toml"
    assert 'version = "1.0.0"' in manifest.read_text(encoding="utf-8")
