from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path

import pytest

from plugin.core.host import evict_cached_plugin_modules
from plugin.core.state import state
from plugin.server.application.plugins import source_switch as source_switch_module
from plugin.server.application.plugins.source_switch import (
    SourceSwitchError,
    SourceSwitchRequest,
    switch_builtin_source,
)

pytestmark = pytest.mark.plugin_unit


@pytest.fixture(autouse=True)
def _restore_study_companion_module_cache():
    """Keep source-switch cache eviction from leaking into later tests."""

    plugin_id = "study_companion"
    module_roots = (f"plugins.{plugin_id}", f"plugin.plugins.{plugin_id}")
    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if any(name == root or name.startswith(f"{root}.") for root in module_roots)
    }
    saved_parent_children = {
        parent_name: getattr(sys.modules.get(parent_name), plugin_id, None)
        for parent_name in ("plugins", "plugin.plugins")
    }
    try:
        yield
    finally:
        evict_cached_plugin_modules(plugin_id)
        sys.modules.update(saved_modules)
        for parent_name, child_module in saved_parent_children.items():
            parent_module = sys.modules.get(parent_name)
            if parent_module is not None and child_module is not None:
                setattr(parent_module, plugin_id, child_module)


def _plan(token: str) -> dict[str, object]:
    return {
        "action": "override_builtin",
        "plugin_id": "study_companion",
        "current_source": "builtin",
        "target_source": "market",
        "confirmation_token": token,
    }


@pytest.mark.asyncio
async def test_switch_rejects_dotted_plugin_id_before_transaction_callbacks(
    tmp_path: Path,
) -> None:
    async def unexpected(*_args: object) -> object:
        raise AssertionError("transaction callback must not run")

    with pytest.raises(ValueError, match="must not contain dots"):
        await switch_builtin_source(
            SourceSwitchRequest(
                plugin_id="study.companion",
                staged_plugin_dir=tmp_path / ".staging",
                target_plugin_dir=tmp_path / "study.companion",
                confirmation_token="token",
            ),
            rebuild_plan=unexpected,
            read_lock_snapshot=unexpected,
            commit_lock=unexpected,
            restore_lock=unexpected,
            clear_user_source=unexpected,
            refresh_registry=unexpected,
            validate_promoted_source=unexpected,
            is_running=unexpected,
            stop=unexpected,
            start=unexpected,
        )


@pytest.mark.asyncio
async def test_switch_builtin_source_promotes_user_code_without_touching_state(
    tmp_path: Path,
) -> None:
    exec_root = tmp_path / "exec"
    staging = exec_root / ".study_companion.staging-test"
    target = exec_root / "study_companion"
    staging.mkdir(parents=True)
    (staging / "plugin.toml").write_text("[plugin]\nid='study_companion'\n", encoding="utf-8")
    state_db = tmp_path / "plugins" / "study_companion" / "data" / "study.db"
    state_db.parent.mkdir(parents=True)
    state_db.write_bytes(b"database-before")
    events: list[str] = []
    running = True
    plugins_backup = copy.deepcopy(state.plugins)

    async def refresh() -> None:
        source = target if target.exists() else tmp_path / "builtin" / "study_companion"
        with state.acquire_plugins_write_lock():
            state.plugins["study_companion"] = {
                "config_path": str(source / "plugin.toml"),
                "effective_source": "user" if target.exists() else "builtin",
            }

    async def is_running(_plugin_id: str) -> bool:
        return running

    async def stop(_plugin_id: str) -> None:
        nonlocal running
        running = False
        events.append("stop")

    async def start(_plugin_id: str) -> None:
        nonlocal running
        running = True
        events.append("start")

    try:
        result = await switch_builtin_source(
            SourceSwitchRequest(
                plugin_id="study_companion",
                staged_plugin_dir=staging,
                target_plugin_dir=target,
                confirmation_token="token",
            ),
            rebuild_plan=lambda: _async_value(_plan("token")),
            read_lock_snapshot=lambda: _async_value({"old": True}),
            commit_lock=lambda: _async_value(None),
            restore_lock=lambda _snapshot: _async_value(None),
            clear_user_source=lambda: _async_value(None),
            refresh_registry=refresh,
            validate_promoted_source=lambda: _async_value(None),
            is_running=is_running,
            stop=stop,
            start=start,
        )

        assert result.code == "override_completed"
        assert result.effective_source == "market"
        assert events == ["stop", "start"]
        assert (target / "plugin.toml").is_file()
        assert state_db.read_bytes() == b"database-before"
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)


@pytest.mark.asyncio
async def test_switch_start_failure_rolls_back_code_profile_lock_and_builtin_runtime(
    tmp_path: Path,
) -> None:
    exec_root = tmp_path / "exec"
    staging = exec_root / ".study_companion.staging-test"
    target = exec_root / "study_companion"
    staging.mkdir(parents=True)
    (staging / "plugin.toml").write_text("new", encoding="utf-8")
    profiles_root = tmp_path / "profiles"
    staged_profile = profiles_root / ".study_companion.staging-test"
    target_profile = profiles_root / "study_companion"
    staged_profile.mkdir(parents=True)
    (staged_profile / "default.toml").write_text("profile", encoding="utf-8")
    builtin_config = tmp_path / "builtin" / "study_companion" / "plugin.toml"
    builtin_config.parent.mkdir(parents=True)
    builtin_config.write_text("builtin", encoding="utf-8")
    state_db = tmp_path / "plugins" / "study_companion" / "data" / "study.db"
    state_db.parent.mkdir(parents=True)
    state_db.write_bytes(b"stable-db")
    running = True
    lock_value: object = {"source": "builtin"}
    plugins_backup = copy.deepcopy(state.plugins)

    async def refresh() -> None:
        source = target if target.exists() else builtin_config.parent
        with state.acquire_plugins_write_lock():
            state.plugins["study_companion"] = {"config_path": str(source / "plugin.toml")}

    async def is_running(_plugin_id: str) -> bool:
        return running

    async def stop(_plugin_id: str) -> None:
        nonlocal running
        running = False

    async def start(_plugin_id: str) -> None:
        nonlocal running
        if target.exists():
            raise RuntimeError("market failed to start")
        running = True

    async def commit_lock() -> None:
        nonlocal lock_value
        lock_value = {"source": "market"}

    async def restore_lock(snapshot: object) -> None:
        nonlocal lock_value
        lock_value = snapshot

    try:
        with pytest.raises(SourceSwitchError) as exc_info:
            await switch_builtin_source(
                SourceSwitchRequest(
                    plugin_id="study_companion",
                    staged_plugin_dir=staging,
                    target_plugin_dir=target,
                    confirmation_token="token",
                    staged_profile_dir=staged_profile,
                    target_profile_dir=target_profile,
                ),
                rebuild_plan=lambda: _async_value(_plan("token")),
                read_lock_snapshot=lambda: _async_value(lock_value),
                commit_lock=commit_lock,
                restore_lock=restore_lock,
                clear_user_source=lambda: _async_value(None),
                refresh_registry=refresh,
                validate_promoted_source=lambda: _async_value(None),
                is_running=is_running,
                stop=stop,
                start=start,
            )

        assert exc_info.value.code == "override_start_failed"
        assert exc_info.value.rollback_code == "override_rollback_completed"
        assert exc_info.value.as_payload() == {
            "code": "override_start_failed",
            "stage": "start_market",
            "error_type": "RuntimeError",
            "rollback_code": "override_rollback_completed",
            "running": False,
            "restored": True,
        }
        assert target.exists() is False
        assert target_profile.exists() is False
        assert lock_value == {"source": "builtin"}
        assert running is True
        assert state_db.read_bytes() == b"stable-db"
        with state.acquire_plugins_read_lock():
            assert state.plugins["study_companion"]["config_path"] == str(builtin_config)
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)


@pytest.mark.asyncio
async def test_switch_registry_load_failure_rolls_back_disabled_builtin(
    tmp_path: Path,
) -> None:
    plugin_id = "study_companion"
    exec_root = tmp_path / "exec"
    staging = exec_root / ".study_companion.staging-test"
    target = exec_root / plugin_id
    staging.mkdir(parents=True)
    (staging / "plugin.toml").write_text("market", encoding="utf-8")
    builtin_config = tmp_path / "builtin" / plugin_id / "plugin.toml"
    builtin_config.parent.mkdir(parents=True)
    builtin_config.write_text("builtin", encoding="utf-8")
    plugins_backup = copy.deepcopy(state.plugins)
    lock_value: object = {"source": "builtin"}
    start_calls: list[str] = []

    async def refresh() -> None:
        with state.acquire_plugins_write_lock():
            if target.exists():
                state.plugins[plugin_id] = {
                    "config_path": str(target / "plugin.toml"),
                    "runtime_load_state": "failed",
                    "runtime_load_error_type": "ModuleNotFoundError",
                    "runtime_load_error_phase": "import_module",
                }
            else:
                state.plugins[plugin_id] = {
                    "config_path": str(builtin_config),
                    "runtime_load_state": "ready",
                }

    async def commit_lock() -> None:
        nonlocal lock_value
        lock_value = {"source": "market"}

    async def restore_lock(snapshot: object) -> None:
        nonlocal lock_value
        lock_value = snapshot

    async def start(_plugin_id: str) -> None:
        start_calls.append(_plugin_id)

    try:
        with pytest.raises(SourceSwitchError) as exc_info:
            await switch_builtin_source(
                SourceSwitchRequest(
                    plugin_id=plugin_id,
                    staged_plugin_dir=staging,
                    target_plugin_dir=target,
                    confirmation_token="token",
                ),
                rebuild_plan=lambda: _async_value(_plan("token")),
                read_lock_snapshot=lambda: _async_value(lock_value),
                commit_lock=commit_lock,
                restore_lock=restore_lock,
                clear_user_source=lambda: _async_value(None),
                refresh_registry=refresh,
                validate_promoted_source=lambda: _async_value(None),
                is_running=lambda _plugin_id: _async_value(False),
                stop=lambda _plugin_id: _async_value(None),
                start=start,
            )

        assert exc_info.value.stage == "refresh_registry"
        assert exc_info.value.rollback_code == "override_rollback_completed"
        assert "ModuleNotFoundError during import_module" in str(exc_info.value.cause)
        assert target.exists() is False
        assert lock_value == {"source": "builtin"}
        assert start_calls == []
        with state.acquire_plugins_read_lock():
            assert state.plugins[plugin_id]["config_path"] == str(builtin_config)
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cancel_stage",
    (
        "stop_builtin",
        "promote_plugin",
        "promote_profile",
        "write_lock",
        "refresh_registry",
        "validate_promoted_source",
        "start_market",
    ),
)
async def test_switch_cancellation_completes_rollback_and_reraises_cancelled_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel_stage: str,
) -> None:
    plugin_id = "study_companion"
    exec_root = tmp_path / "exec"
    staging = exec_root / ".study_companion.staging-test"
    target = exec_root / plugin_id
    staging.mkdir(parents=True)
    (staging / "plugin.toml").write_text("market", encoding="utf-8")
    profiles_root = tmp_path / "profiles"
    staged_profile = profiles_root / ".study_companion.staging-test"
    target_profile = profiles_root / plugin_id
    staged_profile.mkdir(parents=True)
    (staged_profile / "default.toml").write_text("profile", encoding="utf-8")
    builtin_config = tmp_path / "builtin" / plugin_id / "plugin.toml"
    builtin_config.parent.mkdir(parents=True)
    builtin_config.write_text("builtin", encoding="utf-8")
    state_db = tmp_path / "state" / plugin_id / "data" / "study.db"
    state_db.parent.mkdir(parents=True)
    state_db.write_bytes(b"persistent-state")

    plugins_backup = copy.deepcopy(state.plugins)
    running = True
    cancelled = False
    lock_value: object = {"source": "builtin"}
    restore_calls: list[object] = []
    clear_calls = 0

    def cancel_once(stage: str) -> None:
        nonlocal cancelled
        if cancel_stage == stage and not cancelled:
            cancelled = True
            raise asyncio.CancelledError()

    original_promote = source_switch_module._promote_directory_sync

    def promote(staging_path: Path, target_path: Path) -> None:
        original_promote(staging_path, target_path)
        if target_path == target:
            cancel_once("promote_plugin")
        elif target_path == target_profile:
            cancel_once("promote_profile")

    async def refresh() -> None:
        source = target if target.exists() else builtin_config.parent
        with state.acquire_plugins_write_lock():
            state.plugins[plugin_id] = {"config_path": str(source / "plugin.toml")}
        cancel_once("refresh_registry")

    async def validate_promoted_source() -> None:
        cancel_once("validate_promoted_source")

    async def is_running(_plugin_id: str) -> bool:
        return running

    async def stop(_plugin_id: str) -> None:
        nonlocal running
        running = False
        cancel_once("stop_builtin")

    async def start(_plugin_id: str) -> None:
        nonlocal running
        running = True
        cancel_once("start_market")

    async def commit_lock() -> None:
        nonlocal lock_value
        lock_value = {"source": "market"}
        cancel_once("write_lock")

    async def restore_lock(snapshot: object) -> None:
        nonlocal lock_value
        restore_calls.append(snapshot)
        lock_value = snapshot

    async def clear_user_source() -> None:
        nonlocal clear_calls
        clear_calls += 1

    monkeypatch.setattr(source_switch_module, "_promote_directory_sync", promote)
    try:
        with state.acquire_plugins_write_lock():
            state.plugins[plugin_id] = {"config_path": str(builtin_config)}

        with pytest.raises(asyncio.CancelledError):
            await switch_builtin_source(
                SourceSwitchRequest(
                    plugin_id=plugin_id,
                    staged_plugin_dir=staging,
                    target_plugin_dir=target,
                    confirmation_token="token",
                    staged_profile_dir=staged_profile,
                    target_profile_dir=target_profile,
                ),
                rebuild_plan=lambda: _async_value(_plan("token")),
                read_lock_snapshot=lambda: _async_value(lock_value),
                commit_lock=commit_lock,
                restore_lock=restore_lock,
                clear_user_source=clear_user_source,
                refresh_registry=refresh,
                validate_promoted_source=validate_promoted_source,
                is_running=is_running,
                stop=stop,
                start=start,
            )

        assert cancelled is True
        assert target.exists() is False
        assert target_profile.exists() is False
        assert lock_value == {"source": "builtin"}
        assert running is True
        assert state_db.read_bytes() == b"persistent-state"
        with state.acquire_plugins_read_lock():
            assert state.plugins[plugin_id]["config_path"] == str(builtin_config)
        lock_was_attempted = cancel_stage in {
            "write_lock",
            "refresh_registry",
            "validate_promoted_source",
            "start_market",
        }
        assert restore_calls == ([{"source": "builtin"}] if lock_was_attempted else [])
        assert clear_calls == (1 if lock_was_attempted else 0)
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)


@pytest.mark.asyncio
async def test_switch_rejects_changed_source_before_mutating(tmp_path: Path) -> None:
    exec_root = tmp_path / "exec"
    staging = exec_root / ".study_companion.staging-test"
    target = exec_root / "study_companion"
    staging.mkdir(parents=True)

    plan = _plan("token")
    plan["current_source"] = "market"
    with pytest.raises(SourceSwitchError) as exc_info:
        await switch_builtin_source(
            SourceSwitchRequest(
                plugin_id="study_companion",
                staged_plugin_dir=staging,
                target_plugin_dir=target,
                confirmation_token="token",
            ),
            rebuild_plan=lambda: _async_value(plan),
            read_lock_snapshot=lambda: _async_value(None),
            commit_lock=lambda: _async_value(None),
            restore_lock=lambda _snapshot: _async_value(None),
            clear_user_source=lambda: _async_value(None),
            refresh_registry=lambda: _async_value(None),
            validate_promoted_source=lambda: _async_value(None),
            is_running=lambda _plugin_id: _async_value(False),
            stop=lambda _plugin_id: _async_value(None),
            start=lambda _plugin_id: _async_value(None),
        )

    assert exc_info.value.code == "override_source_changed"
    assert staging.is_dir()
    assert target.exists() is False


@pytest.mark.asyncio
async def test_switch_failure_before_lock_commit_does_not_restore_lock_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exec_root = tmp_path / "exec"
    staging = exec_root / ".study_companion.staging-test"
    target = exec_root / "study_companion"
    staging.mkdir(parents=True)
    calls: list[str] = []

    async def record(name: str) -> None:
        calls.append(name)

    def promote_fails(_staging: Path, _target: Path) -> None:
        raise PermissionError("promote failed")

    monkeypatch.setattr(source_switch_module, "_promote_directory_sync", promote_fails)

    with pytest.raises(SourceSwitchError) as exc_info:
        await switch_builtin_source(
            SourceSwitchRequest(
                plugin_id="study_companion",
                staged_plugin_dir=staging,
                target_plugin_dir=target,
                confirmation_token="token",
            ),
            rebuild_plan=lambda: _async_value(_plan("token")),
            read_lock_snapshot=lambda: _async_value({"source": "builtin"}),
            commit_lock=lambda: record("commit"),
            restore_lock=lambda _snapshot: record("restore"),
            clear_user_source=lambda: record("clear"),
            refresh_registry=lambda: record("refresh"),
            validate_promoted_source=lambda: record("validate"),
            is_running=lambda _plugin_id: _async_value(False),
            stop=lambda _plugin_id: _async_value(None),
            start=lambda _plugin_id: _async_value(None),
        )

    assert exc_info.value.rollback_code == "override_rollback_completed"
    assert calls == ["refresh"]
    assert staging.is_dir()
    assert target.exists() is False


async def _async_value(value):
    return value
