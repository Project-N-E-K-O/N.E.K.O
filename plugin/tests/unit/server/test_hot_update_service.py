from __future__ import annotations

import pytest

from plugin.core.state import state
from plugin.server.application.config import hot_update_service as module
from plugin.server.domain.errors import ServerDomainError


class _QuarantinedHost:
    _neko_startup_quarantined = True

    def __init__(self) -> None:
        self.update_calls = 0

    async def send_config_update(self, **_kwargs: object) -> dict[str, object]:
        self.update_calls += 1
        return {"handler_called": True}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_temporary_update_rejects_quarantined_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _QuarantinedHost()
    monkeypatch.setattr(state, "plugin_hosts", {"demo": host})

    with pytest.raises(ServerDomainError) as exc_info:
        await module.hot_update_plugin_config(
            plugin_id="demo",
            updates={"runtime": {"enabled": True}},
            mode="temporary",
        )

    assert exc_info.value.code == "PLUGIN_NOT_RUNNING"
    assert host.update_calls == 0


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_permanent_update_persists_without_reloading_quarantined_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _QuarantinedHost()
    persisted_calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(state, "plugin_hosts", {"demo": host})
    monkeypatch.setattr(
        module,
        "update_plugin_config",
        lambda plugin_id, updates: (
            persisted_calls.append((plugin_id, updates))
            or {"success": True, "plugin_id": plugin_id}
        ),
    )
    monkeypatch.setattr(
        module,
        "load_plugin_config",
        lambda _plugin_id: {"config": {"runtime": {"enabled": True}}},
    )

    result = await module.hot_update_plugin_config(
        plugin_id="demo",
        updates={"runtime": {"enabled": True}},
        mode="permanent",
    )

    assert result["hot_reloaded"] is False
    assert persisted_calls == [("demo", {"runtime": {"enabled": True}})]
    assert host.update_calls == 0


class _RunningHost:
    def __init__(self) -> None:
        self.configs: list[dict[str, object]] = []

    async def send_config_update(self, *, config: dict[str, object], **_kwargs: object) -> dict[str, object]:
        self.configs.append(config)
        return {"success": True}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["temporary", "permanent"])
async def test_hot_update_drops_reserved_permissions_table(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    host = _RunningHost()
    persisted: list[dict[str, object]] = []
    monkeypatch.setattr(module, "_get_host_sync", lambda _plugin_id: host)
    monkeypatch.setattr(
        module,
        "update_plugin_config",
        lambda plugin_id, updates: persisted.append(updates) or {"success": True},
    )
    monkeypatch.setattr(
        module,
        "load_plugin_config",
        lambda _plugin_id: {"config": {"runtime": {"enabled": True}}},
    )

    await module.hot_update_plugin_config(
        plugin_id="demo",
        updates={"runtime": {"enabled": True}, "permissions": {"ask": {}}},
        mode=mode,
    )

    assert host.configs == [{"runtime": {"enabled": True}}]
    assert persisted == ([{"runtime": {"enabled": True}}] if mode == "permanent" else [])


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_permanent_update_with_profile_targets_profile_not_base_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_calls: list[object] = []
    profile_calls: list[tuple[str, str, dict[str, object]]] = []

    class _RunningHost:
        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []

        async def send_config_update(
            self,
            *,
            config: dict[str, object],
            mode: str = "temporary",
            profile: str | None = None,
            timeout: float = 10.0,
            replace: bool = False,
        ) -> dict[str, object]:
            self.sent.append(
                {
                    "config": config,
                    "mode": mode,
                    "profile": profile,
                    "timeout": timeout,
                    "replace": replace,
                }
            )
            return {"handler_called": True}

    def _fake_update_profile(*, plugin_id: str, profile_name: str, updates: dict[str, object]) -> dict[str, object]:
        profile_calls.append((plugin_id, profile_name, updates))
        return {
            "success": True,
            "plugin_id": plugin_id,
            "profile": profile_name,
            "active_profile": "work",
            "config": updates,
        }

    monkeypatch.setattr(module, "update_plugin_config", lambda *a, **k: base_calls.append((a, k)))
    monkeypatch.setattr(module, "update_profile_config", _fake_update_profile)
    monkeypatch.setattr(module, "load_plugin_config", lambda _pid: {"config": {"feature": {"x": 2, "y": 3}}})

    # 1. Host stopped -> writes profile, does not touch base config.
    monkeypatch.setattr(state, "plugin_hosts", {})
    stopped_res = await module.hot_update_plugin_config(
        plugin_id="demo",
        updates={"feature": {"x": 2}},
        mode="permanent",
        profile="work",
    )
    assert stopped_res["hot_reloaded"] is False
    assert stopped_res["profile"] == "work"
    assert base_calls == []
    assert len(profile_calls) == 1

    # 2. Host running and profile != active_profile -> updates profile only, skips live reload.
    host = _RunningHost()
    monkeypatch.setattr(state, "plugin_hosts", {"demo": host})
    inactive_res = await module.hot_update_plugin_config(
        plugin_id="demo",
        updates={"feature": {"x": 9}},
        mode="permanent",
        profile="other",
    )
    assert inactive_res["hot_reloaded"] is False
    assert inactive_res["profile"] == "other"
    assert host.sent == []
    assert base_calls == []

    # 3. Host running and profile == active_profile -> updates profile and hot reloads full snapshot (retaining y=3).
    active_res = await module.hot_update_plugin_config(
        plugin_id="demo",
        updates={"feature": {"x": 2}},
        mode="permanent",
        profile="work",
    )
    assert active_res["hot_reloaded"] is True
    assert active_res["profile"] == "work"
    assert host.sent == [
        {"config": {"feature": {"x": 2, "y": 3}}, "mode": "temporary", "profile": "work", "timeout": 10.0, "replace": True}
    ]
    assert base_calls == []

    # 3b. Host running and alias profile shares active file (affected_active=True) -> triggers hot reload.
    host.sent.clear()
    monkeypatch.setattr(
        module,
        "update_profile_config",
        lambda *, plugin_id, profile_name, updates: {
            "success": True,
            "plugin_id": plugin_id,
            "profile": profile_name,
            "active_profile": "work",
            "affected_active": True,
            "config": updates,
        },
    )
    alias_res = await module.hot_update_plugin_config(
        plugin_id="demo",
        updates={"feature": {"x": 2}},
        mode="permanent",
        profile="alias_of_work",
    )
    assert alias_res["hot_reloaded"] is True
    assert len(host.sent) == 1
    assert host.sent[0]["replace"] is True

    # 4. Whitespace profile raises INVALID_ARGUMENT.
    with pytest.raises(ServerDomainError) as exc_info:
        await module.hot_update_plugin_config(
            plugin_id="demo",
            updates={"feature": {"x": 2}},
            mode="permanent",
            profile="   ",
        )
    assert exc_info.value.code == "INVALID_ARGUMENT"
