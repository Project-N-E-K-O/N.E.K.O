from __future__ import annotations

import asyncio

import pytest

from plugin.server.application.install_source.reconciler import StartupReconciler
from plugin.server.application.plugins import operation_lock as operation_lock_module
from plugin.server.application.plugins.operation_lock import plugin_operation_lock


pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
async def test_startup_reconcile_waits_for_plugin_operation_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    manager = type(
        "Manager",
        (),
        {
            "load": lambda self: calls.append("load"),
            "reconcile": lambda self: calls.append("reconcile"),
        },
    )()
    monkeypatch.setattr(
        operation_lock_module,
        "_reload_install_source_manager_sync",
        lambda: None,
    )

    async with plugin_operation_lock.hold():
        task = asyncio.create_task(StartupReconciler(manager).run())
        await asyncio.sleep(0)
        assert calls == []

    await task
    assert calls == ["load", "reconcile"]
