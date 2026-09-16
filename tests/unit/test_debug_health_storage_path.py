from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.unit
def test_debug_health_log_uses_fixed_anchor_while_storage_is_blocked(tmp_path, monkeypatch):
    from main_routers import debug_router
    from main_routers import shared_state

    selected_root = tmp_path / "selected" / "N.E.K.O"
    anchor_root = tmp_path / "anchor" / "N.E.K.O"
    manager = SimpleNamespace(
        config_dir=selected_root / "config",
        anchor_root=anchor_root,
    )
    monkeypatch.setenv("NEKO_DEBUG_HEALTH_LOG", "1")
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "migration_pending")
    monkeypatch.setattr(shared_state, "get_config_manager", lambda: manager)

    assert debug_router._resolve_log_path() == anchor_root / "state" / "debug_health.jsonl"


@pytest.mark.unit
def test_debug_health_log_stays_memory_only_when_recovery_anchor_is_unavailable(
    tmp_path,
    monkeypatch,
):
    from main_routers import debug_router
    from main_routers import shared_state

    manager = SimpleNamespace(config_dir=tmp_path / "selected" / "config")
    monkeypatch.setenv("NEKO_DEBUG_HEALTH_LOG", "1")
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "storage_status_unavailable")
    monkeypatch.setattr(shared_state, "get_config_manager", lambda: manager)

    assert debug_router._resolve_log_path() is None


@pytest.mark.unit
def test_debug_health_log_keeps_normal_config_location(tmp_path, monkeypatch):
    from main_routers import debug_router
    from main_routers import shared_state

    config_dir = tmp_path / "selected" / "N.E.K.O" / "config"
    manager = SimpleNamespace(config_dir=config_dir, anchor_root=tmp_path / "anchor")
    monkeypatch.setenv("NEKO_DEBUG_HEALTH_LOG", "1")
    monkeypatch.delenv("NEKO_STORAGE_RECOVERY_MODE", raising=False)
    monkeypatch.setattr(shared_state, "get_config_manager", lambda: manager)

    assert debug_router._resolve_log_path() == Path(config_dir) / "debug_health.jsonl"
