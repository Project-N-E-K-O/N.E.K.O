from __future__ import annotations

from pathlib import Path

import pytest

from plugin.server.application.install_source.manager import InstallSourceError
from plugin.server.application.install_source.models import LockEntry
from plugin.server.application.plugins.installation_transactions.ownership import (
    UninstallOwnershipError,
    can_neko_uninstall,
    require_uninstall_ownership,
)


def _entry(
    *,
    plugin_id: str = "demo",
    directory_name: str = "demo",
    root_id: str = "user",
    channel: str = "imported",
    removed: bool = False,
) -> LockEntry:
    return LockEntry(
        root_id=root_id,  # type: ignore[arg-type]
        directory_name=directory_name,
        plugin_id=plugin_id,
        channel=channel,  # type: ignore[arg-type]
        reason="user_requested",
        installed_at="2026-08-29T00:00:00.000000Z",
        updated_at="2026-08-29T00:00:00.000000Z",
        last_seen_at="2026-08-29T00:00:00.000000Z",
        removed=removed,
    )


def _config_path(
    tmp_path: Path,
    *,
    declared_plugin_id: str = "demo",
    directory_name: str = "demo",
    top_level_id: bool = False,
) -> Path:
    config_path = tmp_path / directory_name / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = (
        f"id = '{declared_plugin_id}'\n"
        if top_level_id
        else f"[plugin]\nid = '{declared_plugin_id}'\n"
    )
    config_path.write_text(
        manifest,
        encoding="utf-8",
    )
    return config_path


class _Manager:
    def __init__(
        self,
        entry: LockEntry | None,
        *,
        degraded: bool = False,
        error: InstallSourceError | None = None,
    ) -> None:
        self.entry = entry
        self.is_degraded = degraded
        self.error = error

    def entry_for_directory(
        self,
        _directory_path: Path,
        *,
        include_removed: bool = False,
    ) -> LockEntry | None:
        assert include_removed is False
        if self.error is not None:
            raise self.error
        return self.entry


@pytest.mark.plugin_unit
@pytest.mark.parametrize("channel", ["imported", "market"])
def test_can_neko_uninstall_only_installer_owned_user_entries(channel: str) -> None:
    assert can_neko_uninstall(_entry(channel=channel)) is True


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (_entry(root_id="builtin", channel="builtin"), False),
        (_entry(channel="manual"), False),
        (_entry(channel="unknown"), False),
        (_entry(root_id="builtin", channel="market"), False),
        (_entry(removed=True), False),
    ],
)
def test_can_neko_uninstall_rejects_unproven_ownership(
    entry: LockEntry,
    expected: bool,
) -> None:
    assert can_neko_uninstall(entry) is expected


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("manager", "expected_code", "expected_status", "expected_reason"),
    [
        (
            None,
            "PLUGIN_UNINSTALL_OWNERSHIP_UNKNOWN",
            409,
            "install_source_manager_unavailable",
        ),
        (
            _Manager(_entry(), degraded=True),
            "INSTALL_SOURCE_READ_ONLY",
            503,
            "install_source_degraded",
        ),
        (
            _Manager(None),
            "PLUGIN_UNINSTALL_OWNERSHIP_UNKNOWN",
            409,
            "active_entry_missing",
        ),
        (
            _Manager(_entry(plugin_id="other")),
            "PLUGIN_UNINSTALL_OWNERSHIP_UNKNOWN",
            409,
            "entry_identity_mismatch",
        ),
        (
            _Manager(_entry(removed=True)),
            "PLUGIN_UNINSTALL_OWNERSHIP_UNKNOWN",
            409,
            "entry_identity_mismatch",
        ),
        (
            _Manager(_entry(channel="unknown")),
            "PLUGIN_UNINSTALL_OWNERSHIP_UNKNOWN",
            409,
            "unsupported_ownership",
        ),
        (
            _Manager(_entry(root_id="builtin", channel="market")),
            "PLUGIN_UNINSTALL_OWNERSHIP_UNKNOWN",
            409,
            "unsupported_ownership",
        ),
    ],
)
def test_require_uninstall_ownership_fails_closed(
    manager: _Manager | None,
    expected_code: str,
    expected_status: int,
    expected_reason: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(UninstallOwnershipError) as captured:
        require_uninstall_ownership(
            manager=manager,
            runtime_plugin_id="demo",
            config_path=_config_path(tmp_path),
        )

    assert captured.value.code == expected_code
    assert captured.value.status_code == expected_status
    assert captured.value.details["reason"] == expected_reason


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("entry", "expected_code", "expected_status"),
    [
        (
            _entry(root_id="builtin", channel="builtin"),
            "PLUGIN_UNINSTALL_BUILTIN_FORBIDDEN",
            403,
        ),
        (_entry(channel="manual"), "PLUGIN_MANUAL_NOT_MANAGED", 409),
    ],
)
def test_require_uninstall_ownership_reports_known_refusals(
    entry: LockEntry,
    expected_code: str,
    expected_status: int,
    tmp_path: Path,
) -> None:
    with pytest.raises(UninstallOwnershipError) as captured:
        require_uninstall_ownership(
            manager=_Manager(entry),
            runtime_plugin_id="demo",
            config_path=_config_path(tmp_path),
        )

    assert captured.value.code == expected_code
    assert captured.value.status_code == expected_status


@pytest.mark.plugin_unit
def test_require_uninstall_ownership_maps_lookup_failure_without_mutation(
    tmp_path: Path,
) -> None:
    manager = _Manager(
        None,
        error=InstallSourceError("PATH_OUTSIDE_ROOTS", "outside roots"),
    )

    with pytest.raises(UninstallOwnershipError) as captured:
        require_uninstall_ownership(
            manager=manager,
            runtime_plugin_id="demo",
            config_path=_config_path(tmp_path),
        )

    assert captured.value.code == "PLUGIN_UNINSTALL_OWNERSHIP_UNKNOWN"
    assert captured.value.details["reason"] == "install_source_lookup_failed"
    assert captured.value.details["install_source_error"] == "PATH_OUTSIDE_ROOTS"


@pytest.mark.plugin_unit
@pytest.mark.parametrize("channel", ["imported", "market"])
def test_require_uninstall_ownership_returns_exact_managed_entry(
    channel: str,
    tmp_path: Path,
) -> None:
    entry = _entry(channel=channel)

    assert (
        require_uninstall_ownership(
            manager=_Manager(entry),
            runtime_plugin_id="demo",
            config_path=_config_path(tmp_path),
        )
        is entry
    )


@pytest.mark.plugin_unit
@pytest.mark.parametrize("channel", ["imported", "market"])
def test_empty_id_placeholder_keeps_exact_managed_entry_uninstallable(
    channel: str,
    tmp_path: Path,
) -> None:
    entry = _entry(plugin_id="", channel=channel)

    assert (
        require_uninstall_ownership(
            manager=_Manager(entry),
            runtime_plugin_id="demo",
            config_path=_config_path(tmp_path),
        )
        is entry
    )


@pytest.mark.plugin_unit
@pytest.mark.parametrize("channel", ["imported", "market"])
def test_runtime_alias_uses_manifest_id_for_installer_owned_entry(
    channel: str,
    tmp_path: Path,
) -> None:
    entry = _entry(
        plugin_id="demo",
        directory_name="demo_1",
        channel=channel,
    )

    assert (
        require_uninstall_ownership(
            manager=_Manager(entry),
            runtime_plugin_id="demo_1",
            config_path=_config_path(
                tmp_path,
                declared_plugin_id="demo",
                directory_name="demo_1",
            ),
        )
        is entry
    )


@pytest.mark.plugin_unit
@pytest.mark.parametrize("channel", ["imported", "market"])
def test_top_level_manifest_id_keeps_installer_owned_entry_uninstallable(
    channel: str,
    tmp_path: Path,
) -> None:
    entry = _entry(channel=channel)

    assert (
        require_uninstall_ownership(
            manager=_Manager(entry),
            runtime_plugin_id="demo",
            config_path=_config_path(tmp_path, top_level_id=True),
        )
        is entry
    )
