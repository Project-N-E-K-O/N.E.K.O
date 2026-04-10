from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.unit
def test_launcher_prepares_cloudsave_runtime_before_starting_services(monkeypatch, tmp_path):
    import launcher

    config_manager = SimpleNamespace(
        app_docs_dir=tmp_path / "N.E.K.O",
        cloudsave_manifest_path=tmp_path / "N.E.K.O" / "cloudsave" / "manifest.json",
    )
    call_order = []
    emitted_events = []

    @contextmanager
    def _fake_fence(_config_manager, *, mode, reason):
        call_order.append(("fence", mode, reason))
        yield {"mode": mode}

    def _fake_bootstrap(_config_manager):
        call_order.append("bootstrap")
        return {"bootstrap": True}

    class _DummyCloudsaveManager:
        def import_if_needed(self, *, reason: str):
            call_order.append(("import", reason))
            return {"success": True, "action": "imported", "requested_reason": reason}

    def _fake_set_root_mode(_config_manager, mode, **updates):
        call_order.append(("set_root_mode", mode, updates))
        return {"mode": mode, **updates}

    monkeypatch.setattr(launcher, "get_config_manager", lambda _app_name: config_manager)
    monkeypatch.setattr(launcher, "cloud_apply_fence", _fake_fence)
    monkeypatch.setattr(launcher, "bootstrap_local_cloudsave_environment", _fake_bootstrap)
    monkeypatch.setattr(launcher, "get_cloudsave_manager", lambda _config_manager: _DummyCloudsaveManager())
    monkeypatch.setattr(launcher, "set_root_mode", _fake_set_root_mode)
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event_type, payload=None: emitted_events.append((event_type, payload)),
    )

    result = launcher._prepare_cloudsave_runtime_for_launch()

    bootstrap_index = call_order.index("bootstrap")
    import_index = call_order.index(("import", "launcher_phase0_prelaunch_import"))
    assert bootstrap_index < import_index
    assert result["import_result"]["action"] == "imported"
    assert emitted_events[-1][0] == "cloudsave_bootstrap_ready"
    assert emitted_events[-1][1]["import_result"]["requested_reason"] == "launcher_phase0_prelaunch_import"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_syncs_memory_server_after_startup_import():
    import main_server

    with patch(
        "main_routers.characters_router.notify_memory_server_reload",
        AsyncMock(return_value=True),
    ) as mock_reload:
        await main_server._sync_memory_server_after_startup_import({"action": "imported"})

    mock_reload.assert_awaited_once_with(reason="Steam Auto-Cloud startup import")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_skips_memory_reload_when_startup_import_did_not_run():
    import main_server

    with patch(
        "main_routers.characters_router.notify_memory_server_reload",
        AsyncMock(return_value=True),
    ) as mock_reload:
        await main_server._sync_memory_server_after_startup_import({"action": "skipped"})

    mock_reload.assert_not_called()
