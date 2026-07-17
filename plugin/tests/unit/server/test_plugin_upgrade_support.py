from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.server.application.plugins.upgrade_support import (
    SafeUpgradeError,
    perform_safe_upgrade,
    run_rollback,
)

pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
async def test_run_rollback_removes_new_directory_restores_backup_and_restarts(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    backup = tmp_path / "demo.bak"
    target.mkdir()
    (target / "new.txt").write_text("new", encoding="utf-8")
    backup.mkdir()
    (backup / "old.txt").write_text("old", encoding="utf-8")
    restarted: list[str] = []

    async def start(plugin_id: str) -> None:
        restarted.append(plugin_id)

    restored = await run_rollback(
        plugin_id="demo",
        target_dir=target,
        backup_dir=backup,
        restart=True,
        start=start,
    )

    assert restored is True
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert restarted == ["demo"]


@pytest.mark.asyncio
async def test_backup_failure_restarts_running_plugin_without_installing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    events: list[str] = []

    async def is_running(plugin_id: str) -> bool:
        return True

    async def stop(plugin_id: str) -> None:
        events.append(f"stop:{plugin_id}")

    async def start(plugin_id: str) -> None:
        events.append(f"start:{plugin_id}")

    async def install_new() -> dict[str, object]:
        events.append("install")
        return {}

    async def validate_new() -> None:
        events.append("validate")

    async def cleanup_backup(path: Path) -> None:
        events.append(f"cleanup:{path.name}")

    def fail_rename(self: Path, destination: Path) -> Path:
        raise PermissionError(destination)

    monkeypatch.setattr(Path, "rename", fail_rename)

    with pytest.raises(SafeUpgradeError) as exc_info:
        await perform_safe_upgrade(
            plan=SimpleNamespace(action="upgrade", plugin_id="demo"),
            target_dir=target,
            install_new=install_new,
            validate_new=validate_new,
            is_running=is_running,
            stop=stop,
            start=start,
            cleanup_backup=cleanup_backup,
        )

    assert exc_info.value.stage == "backup"
    assert exc_info.value.rollback_status == "completed"
    assert events == ["stop:demo", "start:demo"]
