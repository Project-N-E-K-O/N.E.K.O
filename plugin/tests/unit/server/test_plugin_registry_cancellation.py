from __future__ import annotations

import asyncio
from types import SimpleNamespace
import threading

import pytest

from plugin.server.application.plugins.mutation_guard import plugin_mutation_guard
from plugin.server.application.plugins.registry_service import PluginRegistryService


pytestmark = pytest.mark.plugin_unit


async def _checkpoint() -> None:
    event = asyncio.Event()
    asyncio.get_running_loop().call_soon(event.set)
    await event.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ("registry", "plugin"))
async def test_cancelled_registry_refresh_waits_for_worker_before_guard_release(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    service = PluginRegistryService()
    worker_entered = asyncio.Event()
    worker_finished = asyncio.Event()
    release_worker = threading.Event()
    contender_acquired = asyncio.Event()
    release_contender = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def recover():
        return SimpleNamespace(
            manual_recovery_plugin_ids=(),
            block_user_plugin_root=False,
        )

    def refresh_registry_sync(**_kwargs: object) -> dict[str, object]:
        loop.call_soon_threadsafe(worker_entered.set)
        release_worker.wait()
        loop.call_soon_threadsafe(worker_finished.set)
        return {"success": True}

    def refresh_plugin_sync(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        return refresh_registry_sync()

    monkeypatch.setattr(service, "_recover_incomplete_replacements", recover)
    monkeypatch.setattr(service, "_refresh_registry_sync", refresh_registry_sync)
    monkeypatch.setattr(service, "_refresh_plugin_sync", refresh_plugin_sync)

    async def run_refresh() -> dict[str, object]:
        if entrypoint == "plugin":
            return await service.refresh_plugin("demo")
        return await service.refresh_registry()

    async def contend() -> None:
        async with plugin_mutation_guard():
            contender_acquired.set()
            await release_contender.wait()

    refresh_task = asyncio.create_task(run_refresh())
    contender_task: asyncio.Task[None] | None = None
    try:
        await worker_entered.wait()
        refresh_task.cancel()
        await _checkpoint()
        contender_task = asyncio.create_task(contend())
        await _checkpoint()

        assert not refresh_task.done()
        assert not contender_acquired.is_set()

        refresh_task.cancel()
        await _checkpoint()
        assert not refresh_task.done()
        assert not contender_acquired.is_set()

        release_worker.set()
        await worker_finished.wait()
        with pytest.raises(asyncio.CancelledError):
            await refresh_task

        await contender_acquired.wait()
        release_contender.set()
        await contender_task
        async with plugin_mutation_guard():
            pass
    finally:
        release_worker.set()
        release_contender.set()
        tasks = [refresh_task]
        if contender_task is not None:
            tasks.append(contender_task)
        await asyncio.gather(*tasks, return_exceptions=True)
