from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from plugin.server.application.install_source.models import LockEntry
from plugin.server.application.plugins.installation_transactions.manual_takeover import (
    local_manual_takeover_confirmation_token,
    manual_takeover_snapshot_sha256,
)


pytestmark = pytest.mark.plugin_unit


def _manual_entry() -> LockEntry:
    return LockEntry(
        root_id="user",
        directory_name="demo",
        plugin_id="demo",
        channel="manual",
        reason="user_requested",
        installed_at="2026-08-29T00:00:00.000000Z",
        updated_at="2026-08-29T00:00:00.000000Z",
        last_seen_at="2026-08-29T00:00:00.000000Z",
    )


def test_confirmation_binds_package_code_content_and_ownership(tmp_path: Path) -> None:
    target_dir = tmp_path / "demo"
    target_dir.mkdir()
    manifest = target_dir / "plugin.toml"
    manifest.write_text('[plugin]\nid = "demo"\n', encoding="utf-8")
    code = target_dir / "main.py"
    code.write_text("VALUE = 1\n", encoding="utf-8")
    package = tmp_path / "demo.neko-plugin"
    package.write_bytes(b"package-v1")
    entry = _manual_entry()

    original = local_manual_takeover_confirmation_token(
        package_path=package,
        target_dir=target_dir,
        entry=entry,
    )
    code.write_text("VALUE = 2\n", encoding="utf-8")
    content_changed = local_manual_takeover_confirmation_token(
        package_path=package,
        target_dir=target_dir,
        entry=entry,
    )
    ownership_changed = manual_takeover_snapshot_sha256(
        entry=replace(entry, updated_at="2026-08-29T00:00:01.000000Z"),
        target_dir=target_dir,
    )

    assert content_changed != original
    assert ownership_changed != manual_takeover_snapshot_sha256(
        entry=entry,
        target_dir=target_dir,
    )


def test_all_replaced_directory_content_invalidates_takeover_confirmation(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "demo"
    target_dir.mkdir()
    (target_dir / "plugin.toml").write_text('[plugin]\nid = "demo"\n', encoding="utf-8")
    state_file = target_dir / "data" / "user.db"
    state_file.parent.mkdir()
    state_file.write_bytes(b"before")
    entry = _manual_entry()

    original = manual_takeover_snapshot_sha256(entry=entry, target_dir=target_dir)
    state_file.write_bytes(b"after")

    assert (
        manual_takeover_snapshot_sha256(entry=entry, target_dir=target_dir) != original
    )
