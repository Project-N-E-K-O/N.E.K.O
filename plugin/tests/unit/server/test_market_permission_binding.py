"""Grant source binding before a Market replacement.

Grants follow the plugin's install source. Before a Market upgrade or a
Market takeover changes anything, a grant record that has no source yet is
bound to the source of the lock entry the transaction has validated, so the
Market source recorded for the new package is a source change rather than a
first binding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core.plugin_layout import resolve_plugin_layout
from plugin.server.application.install_source.models import SourceDetailMarket
from plugin.server.application.plugins import permission_service
from plugin.server.application.plugins.installation_transactions import (
    replace as replacement_transaction,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import config_paths
from plugin.server.infrastructure.plugin_permissions import PluginSource
from plugin.server.routes import market_bridge


pytestmark = pytest.mark.plugin_unit

PLUGIN_ID = "demo"
MARKET_ID = "market-demo"
PERMISSION = "plugin_sdk.main_agent.memory:read"
SNAPSHOT = "s" * 64


def _entry(channel: str, *, market_id: str = MARKET_ID) -> object:
    detail = None
    if channel == "market":
        detail = SourceDetailMarket(
            plugin_market_id=market_id,
            version="1.0.0",
            package_url="https://example.invalid/demo.neko-plugin",
            package_sha256="",
            payload_hash=None,
            channel="stable",
            published_at="",
        )
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
        source_detail=detail,
    )


@pytest.fixture(autouse=True)
def registry_lookup_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plugin registry is busy: a fresh source lookup finds nothing. The
    binding must not depend on it."""

    def busy(_plugin_id: str):
        raise TimeoutError("registry read lock busy")

    monkeypatch.setattr(config_paths, "_registered_plugin_config_candidates", busy)
    monkeypatch.setattr(
        market_bridge, "manual_takeover_snapshot_sha256", lambda **_kwargs: SNAPSHOT
    )


async def _replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    entry: object,
):
    plugin_dir = tmp_path / "plugins" / PLUGIN_ID
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = plugin_dir / "plugin.toml"
    manifest.write_text(f'[plugin]\nid = "{PLUGIN_ID}"\nversion = "1.0.0"\n', encoding="utf-8")
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

    result = await market_bridge._replace_market_plugin_transaction(
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
        manual_snapshot_sha256=SNAPSHOT if entry.channel == "manual" else "",
    )
    return result, manifest


def _grant_without_source() -> None:
    permission_service.permission_store.decide(PLUGIN_ID, PERMISSION, "granted", source=None)


@pytest.mark.asyncio
async def test_market_takeover_binds_a_source_less_record_before_replacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manual plugin's source-less grants are bound to the manual source
    first, even while the registry lookup times out, so the Market source
    that follows drops them."""
    assert permission_service.current_source(PLUGIN_ID) is None  # lookup unavailable
    _grant_without_source()
    calls: list[str] = []

    result, _manifest = await _replace(tmp_path, monkeypatch, calls, _entry("manual"))

    assert result.install_result == {"installed": True}
    assert calls == ["install_new", f"source_at_install={PluginSource('manual').to_dict()}"]
    # The plugin starts with its new Market source: a source change.
    record = permission_service.permission_store.sync_source(
        PLUGIN_ID, PluginSource("market", MARKET_ID)
    )
    assert PERMISSION not in record.get("permissions", {})


@pytest.mark.asyncio
async def test_market_upgrade_keeps_grants_of_an_unchanged_market_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrading a Market plugin to a newer release of the same Market id is
    not a source change; its grants stay."""
    _grant_without_source()

    await _replace(tmp_path, monkeypatch, [], _entry("market"))

    record = permission_service.permission_store.sync_source(
        PLUGIN_ID, PluginSource("market", MARKET_ID)
    )
    assert record["permissions"][PERMISSION]["state"] == "granted"


@pytest.mark.asyncio
@pytest.mark.parametrize("record", ["source_less", "denied", "cleared", "bound", "absent"])
async def test_market_upgrade_of_an_incomplete_entry_stops_only_for_a_source_less_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: str
) -> None:
    """A Market entry without its Market id has no known source. When a
    source-less record still grants a permission the replacement stops
    unchanged; a record holding only denials or nothing, a bound record, or
    no record lets it go ahead."""
    store = permission_service.permission_store
    if record == "source_less":
        _grant_without_source()
    elif record == "denied":
        store.decide(PLUGIN_ID, PERMISSION, "denied", source=None)
    elif record == "cleared":
        _grant_without_source()
        store.clear(PLUGIN_ID, PERMISSION)
    elif record == "bound":
        permission_service.permission_store.decide(
            PLUGIN_ID, PERMISSION, "granted", source=PluginSource("market", MARKET_ID)
        )
    calls: list[str] = []
    entry = _entry("market", market_id="")

    if record != "source_less":
        result, _manifest = await _replace(tmp_path, monkeypatch, calls, entry)
        assert result.install_result == {"installed": True}
        return

    with pytest.raises(market_bridge._TaskError) as raised:
        await _replace(tmp_path, monkeypatch, calls, entry)
    assert raised.value.code == "plugin_permission_source_unknown"
    assert raised.value.http_status == 409
    assert calls == []
    manifest = tmp_path / "plugins" / PLUGIN_ID / "plugin.toml"
    assert 'version = "1.0.0"' in manifest.read_text(encoding="utf-8")
    assert permission_service.permission_store.read(PLUGIN_ID).get("source") is None


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

    def cannot_bind(_plugin_id, _source):
        raise ServerDomainError(code, "permission store unavailable", 503)

    monkeypatch.setattr(permission_service.permission_store, "bind_unbound_source", cannot_bind)
    calls: list[str] = []

    if proceeds:
        result, manifest = await _replace(tmp_path, monkeypatch, calls, _entry("manual"))
        assert result.install_result == {"installed": True}
        assert calls[0] == "install_new"
        assert 'version = "2.0.0"' in manifest.read_text(encoding="utf-8")
        return

    with pytest.raises(market_bridge._TaskError) as raised:
        await _replace(tmp_path, monkeypatch, calls, _entry("manual"))
    assert raised.value.code == code.lower()
    assert raised.value.http_status == 503
    assert calls == []
    manifest = tmp_path / "plugins" / PLUGIN_ID / "plugin.toml"
    assert 'version = "1.0.0"' in manifest.read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "proceeds"),
    [
        ("PLUGIN_PERMISSIONS_READ_FAILED", False),
        ("PLUGIN_PERMISSIONS_CORRUPT", True),
    ],
)
async def test_incomplete_entry_check_handles_an_unreadable_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, proceeds: bool
) -> None:
    """With an incomplete entry the record is checked for source-less
    grants: a store that cannot be read or saved stops the replacement, a
    damaged or newer one is skipped."""

    def cannot_check(_plugin_id):
        raise ServerDomainError(code, "permission store unavailable", 503)

    monkeypatch.setattr(permission_service.permission_store, "holds_unbound_grant", cannot_check)
    entry = _entry("market", market_id="")
    calls: list[str] = []

    if proceeds:
        result, _manifest = await _replace(tmp_path, monkeypatch, calls, entry)
        assert result.install_result == {"installed": True}
        assert calls[0] == "install_new"
        return

    with pytest.raises(market_bridge._TaskError) as raised:
        await _replace(tmp_path, monkeypatch, calls, entry)
    assert raised.value.code == code.lower()
    assert calls == []
