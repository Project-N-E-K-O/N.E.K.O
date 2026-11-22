from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin._types.errors import ErrorCode
from plugin.core.responses import fail
from plugin.runs import manager, trigger_service


def test_resolve_run_execution_timeout_uses_ctx_entry_timeout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(manager, "RUN_EXECUTION_TIMEOUT", 300.0)

    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": 600}}) == 600.0


def test_resolve_run_execution_timeout_falls_back_for_zero_ctx_timeout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(manager, "RUN_EXECUTION_TIMEOUT", 300.0)

    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": 0}}) == 300.0


def test_resolve_run_execution_timeout_falls_back_for_null_ctx_timeout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(manager, "RUN_EXECUTION_TIMEOUT", 300.0)

    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": None}}) == 300.0


def test_resolve_run_execution_timeout_falls_back_for_invalid_ctx_timeout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(manager, "RUN_EXECUTION_TIMEOUT", 300.0)

    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": "bad"}}) == 300.0
    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": -1}}) == 300.0
    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": True}}) == 300.0


def test_resolve_run_execution_timeout_uses_entry_meta_when_ctx_omitted(
    monkeypatch,
) -> None:
    monkeypatch.setattr(manager, "RUN_EXECUTION_TIMEOUT", 300.0)
    monkeypatch.setattr(
        trigger_service.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=1.0: {
            "demo.slow": SimpleNamespace(
                meta=SimpleNamespace(event_type="plugin_entry", id="slow", timeout=12.5)
            )
        },
    )

    assert (
        manager._resolve_run_execution_timeout(
            {}, plugin_id="demo", entry_id="slow"
        )
        == 12.5
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["TIMEOUT", "DEADLINE_EXCEEDED", "OPERATION_TIMEOUT"])
async def test_execute_run_commits_timeout_status_and_channel_run(
    monkeypatch,
    code: str,
) -> None:
    store = manager.InMemoryRunStore()
    monkeypatch.setattr(manager, "_run_store", store)
    monkeypatch.setattr(manager, "_export_store", manager.InMemoryExportStore())
    monkeypatch.setattr(manager, "_emit_runs", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "_emit_export", lambda *_a, **_k: None)

    captured_args: list[dict[str, object]] = []
    cancelled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        manager,
        "_propagate_cancel_to_plugin",
        lambda plugin_id, run_id: cancelled.append((plugin_id, run_id)),
    )

    async def _fake_trigger(*, plugin_id, entry_id, args, task_id, client_host):
        del plugin_id, entry_id, task_id, client_host
        captured_args.append(args)
        return SimpleNamespace(
            plugin_response=fail(code, "timed out")
        )

    monkeypatch.setattr(manager, "trigger_plugin", _fake_trigger)

    rec = manager.RunRecord(
        run_id="r-timeout-1",
        plugin_id="demo",
        entry_id="slow",
        status="queued",
        created_at=1.0,
        updated_at=1.0,
    )
    store.create(rec)

    await manager._execute_run(
        "r-timeout-1",
        plugin_id="demo",
        entry_id="slow",
        args={"_ctx": {"channel": "ui"}},
        task_id=None,
        client_host=None,
    )

    assert captured_args[0]["_ctx"]["channel"] == "run"
    assert cancelled == [("demo", "r-timeout-1")]
    done = store.get("r-timeout-1")
    assert done is not None
    assert done.status == "timeout"
    assert done.error is not None
    assert done.error.code == code


@pytest.mark.asyncio
async def test_execute_run_guarded_maps_trigger_deadline_to_timeout_status(
    monkeypatch,
) -> None:
    from plugin.core.rpc import RpcDeadlineExceededError

    store = manager.InMemoryRunStore()
    monkeypatch.setattr(manager, "_run_store", store)
    monkeypatch.setattr(manager, "_export_store", manager.InMemoryExportStore())
    monkeypatch.setattr(manager, "_emit_runs", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "_emit_export", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "RUN_EXECUTION_TIMEOUT", 300.0)

    cancelled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        manager,
        "_propagate_cancel_to_plugin",
        lambda plugin_id, run_id: cancelled.append((plugin_id, run_id)),
    )

    class _TimeoutHost:
        def health_check(self):
            return SimpleNamespace(alive=True, status="running", pid=1, exitcode=None)

        async def trigger(self, entry_id, args, timeout):
            del entry_id, args, timeout
            raise RpcDeadlineExceededError("RPC deadline exceeded")

    monkeypatch.setattr(
        trigger_service.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=1.0: {},
    )
    monkeypatch.setattr(
        trigger_service,
        "_resolve_host",
        lambda plugin_id, trace_id: (_TimeoutHost(), None),
    )

    rec = manager.RunRecord(
        run_id="r-timeout-2",
        plugin_id="demo",
        entry_id="slow",
        status="queued",
        created_at=1.0,
        updated_at=1.0,
    )
    store.create(rec)

    await manager._execute_run_guarded(
        "r-timeout-2",
        plugin_id="demo",
        entry_id="slow",
        args={},
        task_id=None,
        client_host=None,
    )

    done = store.get("r-timeout-2")
    assert done is not None
    assert done.status == "timeout"
    assert done.error is not None
    assert done.error.code == ErrorCode.TIMEOUT.name
    assert cancelled == [("demo", "r-timeout-2")]
