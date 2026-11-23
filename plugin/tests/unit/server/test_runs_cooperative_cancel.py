from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.runs import manager as runs_manager

pytestmark = pytest.mark.plugin_unit


@pytest.fixture(autouse=True)
def _isolated_run_store(monkeypatch: pytest.MonkeyPatch) -> None:
    store = runs_manager.InMemoryRunStore()
    exports = runs_manager.InMemoryExportStore()
    monkeypatch.setattr(runs_manager, "_run_store", store)
    monkeypatch.setattr(runs_manager, "_export_store", exports)
    monkeypatch.setattr(runs_manager, "_active_run_tasks", {})
    monkeypatch.setattr(runs_manager, "_emit_runs", lambda *_a, **_kw: None)
    monkeypatch.setattr(runs_manager, "_emit_export", lambda *_a, **_kw: None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remote_code",
    ["CANCELLED", "CANCELED", "CANCELLED_BY_USER"],
)
async def test_cooperative_cancel_commits_canceled_status(
    monkeypatch: pytest.MonkeyPatch,
    remote_code: str,
) -> None:
    run_id = "run-coop-cancel"
    runs_manager._run_store.create(
        runs_manager.RunRecord(
            run_id=run_id,
            plugin_id="demo_plugin",
            entry_id="work",
            status="queued",
            created_at=1.0,
            updated_at=1.0,
        )
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _fake_trigger_plugin(**_kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(
            plugin_response={
                "success": False,
                "error": {"code": remote_code, "message": "Execution cancelled"},
            }
        )

    monkeypatch.setattr(runs_manager, "trigger_plugin", _fake_trigger_plugin)
    monkeypatch.setattr(runs_manager, "_propagate_cancel_to_plugin", lambda *_a: None)

    exec_task = asyncio.create_task(
        runs_manager._execute_run(
            run_id,
            plugin_id="demo_plugin",
            entry_id="work",
            args={},
            task_id=None,
            client_host=None,
        )
    )
    await entered.wait()

    mid = runs_manager.cancel_run(run_id, reason="user stopped")
    assert mid is not None
    assert mid.status == "cancel_requested"
    assert mid.cancel_requested is True

    release.set()
    await exec_task

    final = runs_manager._run_store.get(run_id)
    assert final is not None
    assert final.status == "canceled"
    assert final.error is not None
    assert final.error.code == "CANCELED"
    assert final.finished_at is not None


@pytest.mark.asyncio
async def test_cancel_requested_with_generic_failure_or_exception_commits_canceled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for idx, raise_exc in enumerate((False, True)):
        run_id = f"run-coop-exc-{idx}"
        runs_manager._run_store.create(
            runs_manager.RunRecord(
                run_id=run_id,
                plugin_id="demo_plugin",
                entry_id="work",
                status="queued",
                created_at=1.0,
                updated_at=1.0,
            )
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def _fake_trigger_plugin(**_kwargs):
            entered.set()
            await release.wait()
            if raise_exc:
                raise RuntimeError("interrupted mid-flight")
            return SimpleNamespace(
                plugin_response={
                    "success": False,
                    "error": {"code": "INTERNAL", "message": "failed after cancel"},
                }
            )

        monkeypatch.setattr(runs_manager, "trigger_plugin", _fake_trigger_plugin)
        monkeypatch.setattr(runs_manager, "_propagate_cancel_to_plugin", lambda *_a: None)

        exec_task = asyncio.create_task(
            runs_manager._execute_run(
                run_id,
                plugin_id="demo_plugin",
                entry_id="work",
                args={},
                task_id=None,
                client_host=None,
            )
        )
        await entered.wait()
        runs_manager.cancel_run(run_id, reason="stop now")
        release.set()
        await exec_task

        final = runs_manager._run_store.get(run_id)
        assert final is not None
        assert final.status == "canceled"
        assert final.error is not None
        assert final.error.code == "CANCELED"


@pytest.mark.asyncio
async def test_cancel_requested_but_plugin_succeeds_commits_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-coop-succeeds"
    runs_manager._run_store.create(
        runs_manager.RunRecord(
            run_id=run_id,
            plugin_id="demo_plugin",
            entry_id="work",
            status="queued",
            created_at=1.0,
            updated_at=1.0,
        )
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _fake_trigger_plugin(**_kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(plugin_response={"success": True, "data": {"ok": True}})

    monkeypatch.setattr(runs_manager, "trigger_plugin", _fake_trigger_plugin)
    monkeypatch.setattr(runs_manager, "_propagate_cancel_to_plugin", lambda *_a: None)

    exec_task = asyncio.create_task(
        runs_manager._execute_run(
            run_id,
            plugin_id="demo_plugin",
            entry_id="work",
            args={},
            task_id=None,
            client_host=None,
        )
    )
    await entered.wait()
    runs_manager.cancel_run(run_id, reason="too late")
    release.set()
    await exec_task

    final = runs_manager._run_store.get(run_id)
    assert final is not None
    assert final.status == "succeeded"
    assert final.error is None
