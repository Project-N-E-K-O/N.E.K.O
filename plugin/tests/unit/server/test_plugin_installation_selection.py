from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading

import pytest

from plugin.server.application.plugins.inventory_store import (
    PluginInventoryError,
    get_plugin_installation_state,
    record_managed_installation,
    select_plugin_installation,
)
from plugin.server.application.plugins.installation_selection import (
    PluginInstallationRoots,
    inspect_plugin_installations,
)
from plugin.server.domain.errors import ServerDomainError


pytestmark = pytest.mark.plugin_unit


def _write_manifest(root: Path, plugin_id: str, version: str) -> None:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                f'name = "{plugin_id}"',
                f'version = "{version}"',
                f'entry = "plugin.plugins.{plugin_id}:Plugin"',
            ]
        ),
        encoding="utf-8",
    )


def _path_policy(tmp_path: Path) -> PluginInstallationRoots:
    return PluginInstallationRoots(
        builtin_root=tmp_path / "builtin",
        managed_root=tmp_path / "managed",
        legacy_root=tmp_path / "legacy",
    )


def test_switching_to_builtin_keeps_managed_payload_available(tmp_path: Path) -> None:
    state_path = tmp_path / "plugin-installations.json"
    record_managed_installation(
        "demo",
        directory_name="demo",
        package_id="demo-package",
        source="market",
        path=state_path,
    )

    initial = get_plugin_installation_state("demo", path=state_path)
    assert initial.generation == 1
    assert initial.active_installation_key == "managed:demo"
    assert [slot.installation_key for slot in initial.installations] == ["managed:demo"]

    assert select_plugin_installation(
        "demo",
        installation_key=None,
        expected_generation=initial.generation,
        path=state_path,
    ) is True

    builtin_selected = get_plugin_installation_state("demo", path=state_path)
    assert builtin_selected.generation == 2
    assert builtin_selected.active_installation_key is None
    assert [slot.installation_key for slot in builtin_selected.installations] == ["managed:demo"]

    assert select_plugin_installation(
        "demo",
        installation_key="managed:demo",
        expected_generation=builtin_selected.generation,
        path=state_path,
    ) is True
    managed_selected = get_plugin_installation_state("demo", path=state_path)
    assert managed_selected.generation == 3
    assert managed_selected.active_installation_key == "managed:demo"


def test_installation_selection_rejects_stale_generation_without_writing(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "plugin-installations.json"
    record_managed_installation(
        "demo",
        directory_name="demo",
        package_id="demo-package",
        source="imported",
        path=state_path,
    )
    before = state_path.read_bytes()

    with pytest.raises(PluginInventoryError, match="generation changed"):
        select_plugin_installation(
            "demo",
            installation_key=None,
            expected_generation=0,
            path=state_path,
        )

    assert state_path.read_bytes() == before
    assert json.loads(before)["activation_claims"]["demo"]["installation_key"] == "managed:demo"


def test_installation_selection_rejects_slot_owned_by_another_plugin(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "plugin-installations.json"
    record_managed_installation(
        "other",
        directory_name="other",
        package_id="other-package",
        source="market",
        path=state_path,
    )
    before = state_path.read_bytes()

    with pytest.raises(PluginInventoryError, match="unavailable"):
        select_plugin_installation(
            "demo",
            installation_key="managed:other",
            expected_generation=1,
            path=state_path,
        )

    assert state_path.read_bytes() == before


def test_projection_switches_between_builtin_and_managed_without_exposing_paths(
    tmp_path: Path,
) -> None:
    policy = _path_policy(tmp_path)
    state_path = tmp_path / "plugin-installations.json"
    _write_manifest(policy.builtin_root, "demo", "1.0.0")
    assert policy.managed_root is not None
    _write_manifest(policy.managed_root, "demo", "1.1.0")
    record_managed_installation(
        "demo",
        directory_name="demo",
        package_id="demo-package",
        source="market",
        path=state_path,
    )

    managed = inspect_plugin_installations(
        "demo",
        roots=policy,
        inventory_path=state_path,
    )
    assert managed.active_selection_id == "managed:demo"
    assert [(item.kind, item.version, item.active) for item in managed.candidates] == [
        ("builtin", "1.0.0", False),
        ("managed", "1.1.0", True),
    ]

    select_plugin_installation(
        "demo",
        installation_key=None,
        expected_generation=managed.generation,
        path=state_path,
    )
    builtin = inspect_plugin_installations(
        "demo",
        roots=policy,
        inventory_path=state_path,
    )
    assert builtin.active_selection_id == "builtin:demo"
    assert [item.active for item in builtin.candidates] == [True, False]


def test_builtin_selection_is_projected_as_builtin_even_when_market_record_remains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin import settings
    from plugin.server.application.install_source.models import LockEntry
    from plugin.server.application.plugins.query_service import _attach_install_source

    builtin_root = tmp_path / "builtin"
    config_path = builtin_root / "demo" / "plugin.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    market_entry = LockEntry(
        root_id="user",
        directory_name="demo",
        plugin_id="demo",
        channel="market",
        reason="user_requested",
        installed_at="2026-08-17T00:00:00.000Z",
        updated_at="2026-08-17T00:00:00.000Z",
        last_seen_at="2026-08-17T00:00:00.000Z",
    )
    plugin_info: dict[str, object] = {"config_path": str(config_path)}

    _attach_install_source(
        plugin_info,
        plugin_id="demo",
        by_plugin_id={"demo": market_entry},
        by_location={("user", "demo"): market_entry},
        active_installations={},
    )

    assert plugin_info["install_source"] == {
        "source": "builtin",
        "reason": "user_requested",
        "installed_at": None,
        "source_detail": None,
    }


def test_market_selection_uses_user_root_record_when_directory_matches_builtin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin import settings
    from plugin.server.application.install_source.models import LockEntry
    from plugin.server.application.plugins.inventory_store import ActiveInstallation
    from plugin.server.application.plugins.query_service import _attach_install_source

    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "plugins"
    config_path = user_root / "demo" / "plugin.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(
        settings,
        "MANAGED_PLUGIN_INSTALLATIONS_ROOT",
        tmp_path / "plugin-installations",
    )
    builtin_entry = LockEntry(
        root_id="builtin",
        directory_name="demo",
        plugin_id="demo",
        channel="builtin",
        reason="user_requested",
        installed_at="2026-08-16T00:00:00.000Z",
        updated_at="2026-08-16T00:00:00.000Z",
        last_seen_at="2026-08-16T00:00:00.000Z",
    )
    plugin_info: dict[str, object] = {"config_path": str(config_path)}

    _attach_install_source(
        plugin_info,
        plugin_id="demo",
        by_plugin_id={"demo": builtin_entry},
        by_location={
            ("builtin", "demo"): builtin_entry,
        },
        active_installations={
            "demo": ActiveInstallation(
                installation_key="user:demo",
                installation_kind="legacy",
                directory_name="demo",
                source="market",
                installed_at="2026-08-17T00:00:00.000Z",
            )
        },
    )

    assert plugin_info["install_source"] == {
        "source": "market",
        "reason": "user_requested",
        "installed_at": "2026-08-17T00:00:00.000Z",
        "source_detail": None,
    }


@pytest.mark.asyncio
async def test_running_plugin_switch_stops_selects_and_restarts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin import settings
    from plugin.server.application.plugins import lifecycle_service as module

    policy = _path_policy(tmp_path)
    state_path = tmp_path / "plugin-installations.json"
    _write_manifest(policy.builtin_root, "demo", "1.0.0")
    assert policy.managed_root is not None
    _write_manifest(policy.managed_root, "demo", "1.1.0")
    record_managed_installation(
        "demo",
        directory_name="demo",
        package_id="demo-package",
        source="market",
        path=state_path,
    )
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(state_path))
    monkeypatch.setenv("NEKO_PLUGIN_MUTATION_LOCK_PATH", str(tmp_path / "mutation.lock"))
    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", policy.builtin_root)
    monkeypatch.setattr(settings, "MANAGED_PLUGIN_INSTALLATIONS_ROOT", policy.managed_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", policy.legacy_root)

    running = True
    calls: list[str] = []

    def _is_running(_plugin_id: str) -> bool:
        return running

    async def _stop(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        nonlocal running
        calls.append("stop")
        running = False
        return {"success": True}

    async def _start(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        nonlocal running
        calls.append("start")
        running = True
        return {"success": True}

    async def _refresh(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        calls.append("refresh")
        return {"success": True}

    monkeypatch.setattr(module, "_plugin_is_running_sync", _is_running)
    monkeypatch.setattr(module.plugin_registry_service, "refresh_plugin", _refresh)
    service = module.PluginLifecycleService()
    monkeypatch.setattr(service, "stop_plugin", _stop)
    monkeypatch.setattr(service, "start_plugin", _start)

    response = await service.switch_plugin_installation(
        "demo",
        selection_id="builtin:demo",
        expected_generation=1,
    )

    assert response["active_selection_id"] == "builtin:demo"
    assert response["restarted"] is True
    assert calls == ["stop", "refresh", "start"]
    assert get_plugin_installation_state("demo", path=state_path).active_installation_key is None


@pytest.mark.asyncio
async def test_switch_failure_restores_previous_claim_and_running_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin import settings
    from plugin.server.application.plugins import lifecycle_service as module

    policy = _path_policy(tmp_path)
    state_path = tmp_path / "plugin-installations.json"
    _write_manifest(policy.builtin_root, "demo", "1.0.0")
    assert policy.managed_root is not None
    _write_manifest(policy.managed_root, "demo", "1.1.0")
    record_managed_installation(
        "demo",
        directory_name="demo",
        package_id="demo-package",
        source="market",
        path=state_path,
    )
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(state_path))
    monkeypatch.setenv("NEKO_PLUGIN_MUTATION_LOCK_PATH", str(tmp_path / "mutation.lock"))
    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", policy.builtin_root)
    monkeypatch.setattr(settings, "MANAGED_PLUGIN_INSTALLATIONS_ROOT", policy.managed_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", policy.legacy_root)

    running = True
    refresh_calls = 0

    monkeypatch.setattr(module, "_plugin_is_running_sync", lambda _plugin_id: running)

    async def _stop(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        nonlocal running
        running = False
        return {"success": True}

    async def _start(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        nonlocal running
        running = True
        return {"success": True}

    async def _refresh(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 1:
            raise RuntimeError("injected refresh failure")
        return {"success": True}

    monkeypatch.setattr(module.plugin_registry_service, "refresh_plugin", _refresh)
    service = module.PluginLifecycleService()
    monkeypatch.setattr(service, "stop_plugin", _stop)
    monkeypatch.setattr(service, "start_plugin", _start)

    with pytest.raises(ServerDomainError) as caught:
        await service.switch_plugin_installation(
            "demo",
            selection_id="builtin:demo",
            expected_generation=1,
        )

    assert caught.value.code == "PLUGIN_INSTALLATION_SWITCH_FAILED"
    assert running is True
    assert refresh_calls == 2
    assert (
        get_plugin_installation_state("demo", path=state_path).active_installation_key
        == "managed:demo"
    )


@pytest.mark.asyncio
async def test_switch_cancellation_waits_for_inventory_worker_then_restores_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin import settings
    from plugin.server.application.plugins import lifecycle_service as module
    from plugin.server.application.plugins.mutation_guard import plugin_mutation_guard

    policy = _path_policy(tmp_path)
    state_path = tmp_path / "plugin-installations.json"
    _write_manifest(policy.builtin_root, "demo", "1.0.0")
    assert policy.managed_root is not None
    _write_manifest(policy.managed_root, "demo", "1.1.0")
    record_managed_installation(
        "demo",
        directory_name="demo",
        package_id="demo-package",
        source="market",
        path=state_path,
    )
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(state_path))
    monkeypatch.setenv("NEKO_PLUGIN_MUTATION_LOCK_PATH", str(tmp_path / "mutation.lock"))
    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", policy.builtin_root)
    monkeypatch.setattr(settings, "MANAGED_PLUGIN_INSTALLATIONS_ROOT", policy.managed_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", policy.legacy_root)

    running = True
    worker_written = asyncio.Event()
    release_worker = threading.Event()
    contender_entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    real_select = module.select_plugin_installation

    def _blocked_select(*args: object, **kwargs: object) -> bool:
        changed = real_select(*args, **kwargs)
        loop.call_soon_threadsafe(worker_written.set)
        assert release_worker.wait(timeout=5)
        return changed

    async def _stop(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        nonlocal running
        running = False
        return {"success": True}

    async def _start(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        nonlocal running
        running = True
        return {"success": True}

    async def _refresh(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        return {"success": True}

    async def _contender() -> None:
        async with plugin_mutation_guard():
            contender_entered.set()

    monkeypatch.setattr(module, "select_plugin_installation", _blocked_select)
    monkeypatch.setattr(module, "_plugin_is_running_sync", lambda _plugin_id: running)
    monkeypatch.setattr(module.plugin_registry_service, "refresh_plugin", _refresh)
    service = module.PluginLifecycleService()
    monkeypatch.setattr(service, "stop_plugin", _stop)
    monkeypatch.setattr(service, "start_plugin", _start)

    switch_task = asyncio.create_task(
        service.switch_plugin_installation(
            "demo",
            selection_id="builtin:demo",
            expected_generation=1,
        )
    )
    await worker_written.wait()
    switch_task.cancel()
    contender_task = asyncio.create_task(_contender())
    checkpoint = asyncio.Event()
    loop.call_soon(checkpoint.set)
    await checkpoint.wait()
    switch_task.cancel()

    assert not switch_task.done()
    assert not contender_entered.is_set()

    release_worker.set()
    with pytest.raises(asyncio.CancelledError):
        await switch_task
    await contender_task

    assert contender_entered.is_set()
    assert running is True
    assert (
        get_plugin_installation_state("demo", path=state_path).active_installation_key
        == "managed:demo"
    )
