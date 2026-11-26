from __future__ import annotations

from pathlib import Path

import asyncio

import pytest

from plugin.core import context as context_module
from plugin.core.context import PluginContext
from plugin.server.application import config as config_application


class _Logger:
    def warning(self, *args, **kwargs) -> None:
        self.last_warning = (args, kwargs)

    def debug(self, *args, **kwargs) -> None:
        self.last_debug = (args, kwargs)


class _Instance:
    def __init__(self) -> None:
        self.refreshed: list[dict[str, object]] = []

    def refresh_runtime_config(self, effective_config: dict[str, object]) -> None:
        self.refreshed.append(effective_config)


@pytest.mark.plugin_unit
def test_push_message_fast_mode_true_emits_runtime_deprecation_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_module, "zmq", None)
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )

    with pytest.warns(DeprecationWarning, match="fast_mode.*v0.9"):
        ctx.push_message(parts=[], fast_mode=True)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_effective_config_refreshes_plugin_runtime_helpers(tmp_path: Path) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance

    async def _local_payload(**kwargs: object) -> dict[str, object]:
        return {"config": {"plugin": {"store": {"enabled": True}}}}

    ctx._get_local_config_payload = _local_payload  # type: ignore[method-assign]

    payload = await ctx.get_own_effective_config()

    assert payload == {"config": {"plugin": {"store": {"enabled": True}}}}
    assert ctx._effective_config == {"plugin": {"store": {"enabled": True}}}
    assert instance.refreshed == [{"plugin": {"store": {"enabled": True}}}]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_uncertain_config_cache_is_reloaded_from_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {"optimistic": True}
    ctx._effective_config_uncertain = True

    class _QueryService:
        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            assert plugin_id == "demo"
            return {"plugin_id": plugin_id, "config": {"persisted": True}}

    monkeypatch.setattr(config_application, "ConfigQueryService", _QueryService)

    payload = await ctx.get_own_config()

    assert payload["config"] == {"persisted": True}
    assert ctx._effective_config == {"persisted": True}
    assert ctx._effective_config_uncertain is False


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("failed_operation", ("update", "replace"))
async def test_context_failed_write_preserves_prior_uncertain_cache_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_operation: str,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {"feature": {"generation": 0}}
    outcomes: list[BaseException] = [
        TimeoutError("first write result is unknown"),
        RuntimeError("second write was rejected"),
    ]

    async def _write(**kwargs: object) -> dict[str, object]:
        raise outcomes.pop(0)

    ctx._send_request_and_wait_async = _write  # type: ignore[method-assign]

    first = await ctx.update_own_config({"feature": {"generation": 1}})
    assert first["persisted"] is None

    with pytest.raises(RuntimeError, match="second write was rejected"):
        if failed_operation == "update":
            await ctx.update_own_config({"feature": {"generation": 2}})
        else:
            await ctx.replace_own_config({"feature": {"generation": 2}})

    class _QueryService:
        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            assert plugin_id == "demo"
            return {
                "plugin_id": plugin_id,
                "config": {"feature": {"generation": 0}},
            }

    monkeypatch.setattr(config_application, "ConfigQueryService", _QueryService)

    payload = await ctx.get_own_config()

    assert payload["config"] == {"feature": {"generation": 0}}


@pytest.mark.plugin_unit
def test_context_optimistic_merge_honors_delete_and_replace_markers() -> None:
    base = {
        "mcp_servers": {
            "fetch": {"url": "https://example.test"},
            "keep": {"url": "https://keep.test"},
        },
        "plugin": {"store": {"enabled": False, "kind": "old"}},
    }

    merged = PluginContext._merge_config_copy(
        base,
        {
            "mcp_servers": {"fetch": "__DELETE__"},
            "plugin": {"store": {"__replace__": True, "enabled": True}},
        },
    )

    assert merged == {
        "mcp_servers": {"keep": {"url": "https://keep.test"}},
        "plugin": {"store": {"enabled": True}},
    }
    assert base["mcp_servers"]["fetch"] == {"url": "https://example.test"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_update_own_config_timeout_uses_host_merge_markers(tmp_path: Path) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {
        "mcp_servers": {
            "fetch": {"url": "https://example.test"},
            "keep": {"url": "https://keep.test"},
        }
    }

    async def _timeout(**kwargs: object) -> dict[str, object]:
        raise TimeoutError("slow persistence")

    ctx._send_request_and_wait_async = _timeout  # type: ignore[method-assign]

    payload = await ctx.update_own_config({"mcp_servers": {"fetch": "__DELETE__"}})

    assert payload["persisted"] is None
    assert payload["config"] == {"mcp_servers": {"keep": {"url": "https://keep.test"}}}
    assert ctx._effective_config == {"mcp_servers": {"keep": {"url": "https://keep.test"}}}
    assert ctx._effective_config_uncertain is True


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_replace_own_config_replaces_optimistic_root(tmp_path: Path) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {"stale": {"enabled": False}}
    captured: dict[str, object] = {}

    async def _replace(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"config": {"replacement": {"enabled": True}}}

    ctx._send_request_and_wait_async = _replace  # type: ignore[method-assign]

    payload = await ctx.replace_own_config({"replacement": {"enabled": True}})

    assert captured["request_type"] == "PLUGIN_CONFIG_REPLACE"
    request_data = captured["request_data"]
    assert isinstance(request_data, dict)
    assert isinstance(request_data["_request_deadline_monotonic"], float)
    assert {
        key: value
        for key, value in request_data.items()
        if key != "_request_deadline_monotonic"
    } == {
        "plugin_id": "demo",
        "config": {"replacement": {"enabled": True}},
    }
    assert payload["config"] == {"replacement": {"enabled": True}}
    assert ctx._effective_config == {"replacement": {"enabled": True}}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_replace_timeout_keeps_requested_config_as_unknown_result(tmp_path: Path) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"stale": {"enabled": False}}

    async def _timeout(**kwargs: object) -> dict[str, object]:
        raise TimeoutError("response arrived after the client deadline")

    ctx._send_request_and_wait_async = _timeout  # type: ignore[method-assign]

    with pytest.raises(TimeoutError, match="client deadline"):
        await ctx.replace_own_config({"replacement": {"enabled": True}})

    assert ctx._effective_config == {"replacement": {"enabled": True}}
    assert ctx._effective_config_uncertain is True
    assert instance.refreshed == [{"replacement": {"enabled": True}}]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_update_own_config_rolls_back_rejected_updates(tmp_path: Path) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"plugin": {"store": {"enabled": False}}}

    async def _reject(**kwargs: object) -> dict[str, object]:
        raise RuntimeError("protected config key")

    ctx._send_request_and_wait_async = _reject  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="protected config key"):
        await ctx.update_own_config({"plugin": {"store": {"enabled": True}}})

    assert ctx._effective_config == {"plugin": {"store": {"enabled": False}}}
    assert instance.refreshed == [
        {"plugin": {"store": {"enabled": True}}},
        {"plugin": {"store": {"enabled": False}}},
    ]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_update_own_config_keeps_optimistic_config_when_result_is_cancelled(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"plugin": {"store": {"enabled": False}}}

    async def _cancel(**kwargs: object) -> dict[str, object]:
        raise asyncio.CancelledError()

    ctx._send_request_and_wait_async = _cancel  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await ctx.update_own_config({"plugin": {"store": {"enabled": True}}})

    assert ctx._effective_config == {"plugin": {"store": {"enabled": True}}}
    assert ctx._effective_config_uncertain is True
    assert instance.refreshed == [
        {"plugin": {"store": {"enabled": True}}},
    ]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_profile_write_methods_wire_rpcs_and_refresh_runtime_config(
    tmp_path: Path,
) -> None:
    from plugin.sdk.shared.core.config import PluginConfig
    from plugin.sdk.shared.core.context import SdkContext

    instance = _Instance()
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._instance = instance
    ctx._effective_config = {"stale": True}
    profiles: dict[str, dict[str, object]] = {}
    active_state: dict[str, str | None] = {"active": None}
    sent_rpcs: list[tuple[str, dict[str, object]]] = []

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, timeout, wrap_result
        sent_rpcs.append((request_type, dict(request_data)))
        if request_type == "PLUGIN_CONFIG_PROFILE_UPSERT":
            name = str(request_data["profile_name"])
            cfg = dict(request_data["config"])  # type: ignore[arg-type]
            profiles[name] = cfg
            prev_active = active_state["active"]
            if request_data.get("make_active") or active_state["active"] is None:
                active_state["active"] = name
            affected_active = (active_state["active"] == name) or (prev_active != active_state["active"])
            return {
                "config": cfg,
                "active_profile": active_state["active"],
                "affected_active": affected_active,
            }
        if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
            name = str(request_data["profile_name"])
            active_state["active"] = name
            return {"config_profiles": {"active": name, "files": {k: {} for k in profiles}}}
        if request_type == "PLUGIN_CONFIG_PROFILE_DELETE":
            name = str(request_data["profile_name"])
            prev_active = active_state["active"]
            removed = name in profiles
            profiles.pop(name, None)
            if active_state["active"] == name:
                active_state["active"] = None
            affected_active = bool((removed and prev_active == name) or (prev_active != active_state["active"]))
            return {
                "removed": removed,
                "active_profile": active_state["active"],
                "affected_active": affected_active,
            }
        if request_type == "PLUGIN_CONFIG_PROFILES_GET":
            return {"config_profiles": {"active": active_state["active"], "files": {k: {} for k in profiles}}}
        if request_type == "PLUGIN_CONFIG_PROFILE_GET":
            name = str(request_data["profile_name"])
            return {"config": dict(profiles.get(name, {}))}
        if request_type == "PLUGIN_CONFIG_GET":
            active_name = active_state["active"]
            return {"config": dict(profiles.get(active_name or "", {}))}
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]
    # Force RPC path instead of local disk ConfigQueryService.
    async def _no_local(**_kwargs: object) -> None:
        return None
    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]

    sdk_config = PluginConfig(SdkContext(ctx))
    created = await sdk_config.profile_create("work", {"a": 1}, make_active=True)
    assert created == {"a": 1}
    assert ctx._effective_config == {"a": 1}
    assert instance.refreshed[-1] == {"a": 1}

    ctx._effective_config = {"stale": True}
    updated = await sdk_config.profile_update("work", {"b": 2})
    assert updated == {"a": 1, "b": 2}
    assert ctx._effective_config == {"a": 1, "b": 2}
    assert instance.refreshed[-1] == {"a": 1, "b": 2}

    await sdk_config.profile_create("home", {"a": 9}, make_active=False)
    assert await sdk_config.profile_activate("home") is True
    assert active_state["active"] == "home"
    assert ctx._effective_config == {"a": 9}
    assert instance.refreshed[-1] == {"a": 9}

    assert await sdk_config.profile_delete("work") is True
    assert ctx._effective_config == {"a": 9}
    assert [op for op, _ in sent_rpcs[:4]] == [
        "PLUGIN_CONFIG_PROFILE_UPSERT",
        "PLUGIN_CONFIG_GET",
        "PLUGIN_CONFIG_PROFILE_GET",
        "PLUGIN_CONFIG_PROFILE_UPSERT",
    ]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_inactive_profile_mutations_preserve_temporary_config(
    tmp_path: Path,
) -> None:
    from plugin.sdk.shared.core.config import PluginConfig
    from plugin.sdk.shared.core.context import SdkContext

    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {"temp_only": "live"}

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, request_data, timeout, wrap_result
        if request_type == "PLUGIN_CONFIG_PROFILE_UPSERT":
            return {"config": {"x": 1}, "active_profile": "main", "affected_active": False}
        if request_type == "PLUGIN_CONFIG_PROFILE_DELETE":
            return {"removed": True, "active_profile": "main", "affected_active": False}
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    sdk_config = PluginConfig(SdkContext(ctx))
    await ctx.upsert_own_profile_config("inactive", {"x": 1}, make_active=False)
    assert ctx._effective_config == {"temp_only": "live"}
    assert await sdk_config.dump() == {"temp_only": "live"}

    await ctx.delete_own_profile_config("inactive")
    assert ctx._effective_config == {"temp_only": "live"}
    assert await sdk_config.dump() == {"temp_only": "live"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_activate_profile_keeps_cleared_cache_when_reload_fails(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {"old": True}

    async def _no_local(**_kwargs: object) -> None:
        return None

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, request_data, timeout, wrap_result
        if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
            return {"config_profiles": {"active": "home"}}
        if request_type == "PLUGIN_CONFIG_GET":
            raise RuntimeError("transient read error")
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    res = await ctx.set_own_active_profile("home")
    assert res["config_profiles"]["active"] == "home"
    assert ctx._effective_config is None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_stale_get_own_config_does_not_overwrite_newer_profile_refresh(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    stale_started = asyncio.Event()
    release_stale = asyncio.Event()
    active_profile = {"name": "home"}

    async def _no_local(**_kwargs: object) -> None:
        return None

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, timeout, wrap_result
        if request_type == "PLUGIN_CONFIG_GET":
            if active_profile["name"] == "home":
                stale_started.set()
                await release_stale.wait()
                return {"config": {"profile": "home"}}
            return {"config": {"profile": "work"}}
        if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
            active_profile["name"] = str(request_data["profile_name"])
            return {"config_profiles": {"active": active_profile["name"]}}
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    slow_read = asyncio.create_task(ctx.get_own_config())
    await stale_started.wait()

    await ctx.set_own_active_profile("work")
    assert ctx._effective_config == {"profile": "work"}

    release_stale.set()
    stale_payload = await slow_read
    assert stale_payload == {"config": {"profile": "home"}}
    assert ctx._effective_config == {"profile": "work"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_stale_profile_refresh_does_not_overwrite_newer_profile_refresh(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    first_get_started = asyncio.Event()
    release_first_get = asyncio.Event()
    active_profile = {"name": "home"}

    async def _no_local(**_kwargs: object) -> None:
        return None

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, timeout, wrap_result
        if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
            active_profile["name"] = str(request_data["profile_name"])
            return {"config_profiles": {"active": active_profile["name"]}}
        if request_type == "PLUGIN_CONFIG_GET":
            if active_profile["name"] == "home":
                first_get_started.set()
                await release_first_get.wait()
                return {"config": {"profile": "home"}}
            return {"config": {"profile": "work"}}
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    first_refresh = asyncio.create_task(ctx.set_own_active_profile("home"))
    await first_get_started.wait()

    await ctx.set_own_active_profile("work")
    assert ctx._effective_config == {"profile": "work"}

    release_first_get.set()
    await first_refresh
    assert ctx._effective_config == {"profile": "work"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_failed_stale_profile_refresh_does_not_clear_newer_profile_cache(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    first_get_started = asyncio.Event()
    release_first_get = asyncio.Event()
    active_profile = {"name": "home"}

    async def _no_local(**_kwargs: object) -> None:
        return None

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, timeout, wrap_result
        if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
            active_profile["name"] = str(request_data["profile_name"])
            return {"config_profiles": {"active": active_profile["name"]}}
        if request_type == "PLUGIN_CONFIG_GET":
            if active_profile["name"] == "home":
                first_get_started.set()
                await release_first_get.wait()
                raise RuntimeError("stale refresh failed")
            return {"config": {"profile": "work"}}
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    first_refresh = asyncio.create_task(ctx.set_own_active_profile("home"))
    await first_get_started.wait()

    await ctx.set_own_active_profile("work")
    assert ctx._effective_config == {"profile": "work"}

    release_first_get.set()
    await first_refresh
    assert ctx._effective_config == {"profile": "work"}


async def _wait_for_checkpoint_or_task_failure(
    event: asyncio.Event,
    task: asyncio.Task[object],
    *,
    timeout: float = 5.0,
) -> None:
    waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait(
            {waiter, task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise TimeoutError("Timed out waiting for async checkpoint")
        if task in done and not event.is_set():
            await task
            raise AssertionError("Task finished before reaching checkpoint")
        await waiter
    finally:
        if not waiter.done():
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("update", "replace"))
async def test_context_failed_write_does_not_rollback_over_newer_profile_refresh(
    tmp_path: Path,
    operation: str,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"profile": "home"}
    ctx._effective_config_uncertain = True

    write_started = asyncio.Event()
    release_write = asyncio.Event()

    async def _no_local(**_kwargs: object) -> None:
        return None

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, request_data, timeout, wrap_result
        if request_type in {"PLUGIN_CONFIG_UPDATE", "PLUGIN_CONFIG_REPLACE"}:
            write_started.set()
            await release_write.wait()
            raise RuntimeError("rejected")
        if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
            return {"config_profiles": {"active": "work"}}
        if request_type == "PLUGIN_CONFIG_GET":
            return {"config": {"profile": "work"}}
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    pending_write = asyncio.create_task(
        ctx.update_own_config({"draft": 1})
        if operation == "update"
        else ctx.replace_own_config({"profile": "home", "draft": 1})
    )
    try:
        await _wait_for_checkpoint_or_task_failure(write_started, pending_write)

        await ctx.set_own_active_profile("work")
        assert ctx._effective_config == {"profile": "work"}
        assert ctx._effective_config_uncertain is False

        release_write.set()
        with pytest.raises(RuntimeError, match="rejected"):
            await pending_write
    finally:
        release_write.set()
        if not pending_write.done():
            pending_write.cancel()
        await asyncio.gather(pending_write, return_exceptions=True)

    assert ctx._effective_config == {"profile": "work"}
    assert ctx._effective_config_uncertain is False
    assert instance.refreshed[-1] == {"profile": "work"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("update", "replace"))
async def test_context_stale_write_success_does_not_overwrite_newer_profile_refresh(
    tmp_path: Path,
    operation: str,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"profile": "home"}

    write_started = asyncio.Event()
    release_write = asyncio.Event()

    async def _no_local(**_kwargs: object) -> None:
        return None

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, request_data, timeout, wrap_result
        if request_type in {"PLUGIN_CONFIG_UPDATE", "PLUGIN_CONFIG_REPLACE"}:
            write_started.set()
            await release_write.wait()
            return {"config": {"profile": "home", "draft": 1}}
        if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
            return {"config_profiles": {"active": "work"}}
        if request_type == "PLUGIN_CONFIG_GET":
            return {"config": {"profile": "work"}}
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    pending_write = asyncio.create_task(
        ctx.update_own_config({"draft": 1})
        if operation == "update"
        else ctx.replace_own_config({"profile": "home", "draft": 1})
    )
    try:
        await _wait_for_checkpoint_or_task_failure(write_started, pending_write)

        await ctx.set_own_active_profile("work")
        assert ctx._effective_config == {"profile": "work"}

        release_write.set()
        payload = await pending_write
    finally:
        release_write.set()
        if not pending_write.done():
            pending_write.cancel()
        await asyncio.gather(pending_write, return_exceptions=True)

    assert payload == {"config": {"profile": "home", "draft": 1}}
    assert ctx._effective_config == {"profile": "work"}
    assert ctx._effective_config_uncertain is True
    assert instance.refreshed[-1] == {"profile": "work"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("fail_order", ("b_then_a", "a_then_b"))
async def test_context_overlapping_failed_writes_restore_original_config(
    tmp_path: Path,
    fail_order: str,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"profile": "home"}

    a_started = asyncio.Event()
    b_started = asyncio.Event()
    release_a = asyncio.Event()
    release_b = asyncio.Event()

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, timeout, wrap_result
        assert request_type == "PLUGIN_CONFIG_UPDATE"
        updates = request_data["updates"]
        assert isinstance(updates, dict)
        if "draft_a" in updates:
            a_started.set()
            await release_a.wait()
            raise RuntimeError("write A rejected")
        if "draft_b" in updates:
            b_started.set()
            await release_b.wait()
            raise RuntimeError("write B rejected")
        raise AssertionError(f"Unexpected updates {updates}")

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    task_a = asyncio.create_task(ctx.update_own_config({"draft_a": 1}))
    task_b: asyncio.Task[object] | None = None
    try:
        await _wait_for_checkpoint_or_task_failure(a_started, task_a)
        assert ctx._effective_config == {"profile": "home", "draft_a": 1}

        task_b = asyncio.create_task(ctx.update_own_config({"draft_b": 2}))
        await _wait_for_checkpoint_or_task_failure(b_started, task_b)
        assert ctx._effective_config == {"profile": "home", "draft_a": 1, "draft_b": 2}

        if fail_order == "b_then_a":
            release_b.set()
            with pytest.raises(RuntimeError, match="write B rejected"):
                await task_b
            assert ctx._effective_config == {"profile": "home", "draft_a": 1}

            release_a.set()
            with pytest.raises(RuntimeError, match="write A rejected"):
                await task_a
        else:
            release_a.set()
            with pytest.raises(RuntimeError, match="write A rejected"):
                await task_a
            assert ctx._effective_config == {"profile": "home", "draft_b": 2}

            release_b.set()
            with pytest.raises(RuntimeError, match="write B rejected"):
                await task_b
    finally:
        release_a.set()
        release_b.set()
        for t in (task_a, task_b):
            if t is not None:
                if not t.done():
                    t.cancel()
                await asyncio.gather(t, return_exceptions=True)

    assert ctx._effective_config == {"profile": "home"}
    assert ctx._effective_config_uncertain is False
    assert instance.refreshed[-1] == {"profile": "home"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_precommit_effective_read_does_not_block_replace_own_config(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"profile": "home"}

    replace_started = asyncio.Event()
    release_replace = asyncio.Event()

    async def _local_payload(*, payload_type: str, **_kwargs: object) -> dict[str, object] | None:
        if payload_type == "effective":
            return {"config": {"profile": "home"}}
        return None

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, request_data, timeout, wrap_result
        assert request_type == "PLUGIN_CONFIG_REPLACE"
        replace_started.set()
        await release_replace.wait()
        return {"config": {"profile": "work", "persisted": True}}

    ctx._get_local_config_payload = _local_payload  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    pending_replace = asyncio.create_task(ctx.replace_own_config({"profile": "work"}))
    try:
        await _wait_for_checkpoint_or_task_failure(replace_started, pending_replace)

        read_payload = await ctx.get_own_effective_config(None)
        assert read_payload == {"config": {"profile": "home"}}
        assert ctx._effective_config == {"profile": "work"}

        release_replace.set()
        replace_payload = await pending_replace
    finally:
        release_replace.set()
        if not pending_replace.done():
            pending_replace.cancel()
        await asyncio.gather(pending_replace, return_exceptions=True)

    assert replace_payload == {"config": {"profile": "work", "persisted": True}}
    assert ctx._effective_config == {"profile": "work", "persisted": True}
    assert ctx._effective_config_uncertain is False
    assert instance.refreshed[-1] == {"profile": "work", "persisted": True}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("error_cls", (TimeoutError, asyncio.CancelledError))
async def test_context_replace_timeout_or_cancel_invalidates_inflight_precommit_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_cls: type[BaseException],
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"profile": "home"}

    replace_started = asyncio.Event()
    release_replace = asyncio.Event()
    read_started = asyncio.Event()
    release_read = asyncio.Event()
    disk_state: dict[str, object] = {"profile": "home"}

    class _QueryService:
        async def get_plugin_effective_config(
            self,
            *,
            plugin_id: str,
            profile_name: str | None = None,
        ) -> dict[str, object]:
            assert plugin_id == "demo"
            assert profile_name is None
            read_started.set()
            await release_read.wait()
            return {"config": {"profile": "home"}}

        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            assert plugin_id == "demo"
            return {"plugin_id": plugin_id, "config": dict(disk_state)}

    async def _failing_replace(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, request_data, timeout, wrap_result
        assert request_type == "PLUGIN_CONFIG_REPLACE"
        replace_started.set()
        await release_replace.wait()
        raise error_cls("replace failed or cancelled")

    monkeypatch.setattr(config_application, "ConfigQueryService", _QueryService)
    ctx._send_request_and_wait_async = _failing_replace  # type: ignore[method-assign]

    pending_replace = asyncio.create_task(ctx.replace_own_config({"profile": "work"}))
    slow_read: asyncio.Task[dict[str, object]] | None = None
    try:
        await _wait_for_checkpoint_or_task_failure(replace_started, pending_replace)

        slow_read = asyncio.create_task(ctx.get_own_effective_config(None))
        await _wait_for_checkpoint_or_task_failure(read_started, slow_read)

        release_replace.set()
        with pytest.raises(error_cls):
            await pending_replace
        assert ctx._effective_config == {"profile": "work"}
        assert ctx._effective_config_uncertain is True

        disk_state = {"profile": "work", "persisted": True}
        release_read.set()
        stale_read = await slow_read
    finally:
        release_replace.set()
        release_read.set()
        for task in (pending_replace, slow_read):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    assert stale_read == {"config": {"profile": "home"}}
    assert ctx._effective_config == {"profile": "work"}
    assert ctx._effective_config_uncertain is True

    fresh = await ctx.get_own_config()
    assert fresh["config"] == {"profile": "work", "persisted": True}
    assert ctx._effective_config == {"profile": "work", "persisted": True}
    assert ctx._effective_config_uncertain is False


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_persisted_false_does_not_resurrect_rejected_write(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"profile": "home"}

    a_started = asyncio.Event()
    b_started = asyncio.Event()
    release_a = asyncio.Event()
    release_b = asyncio.Event()

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, timeout, wrap_result
        assert request_type == "PLUGIN_CONFIG_UPDATE"
        updates = request_data["updates"]
        assert isinstance(updates, dict)
        if "draft_a" in updates:
            a_started.set()
            await release_a.wait()
            raise RuntimeError("write A rejected")
        if "draft_b" in updates:
            b_started.set()
            await release_b.wait()
            return {"config": {"draft_b": 2}, "persisted": False}
        raise AssertionError(f"Unexpected updates {updates}")

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    task_a = asyncio.create_task(ctx.update_own_config({"draft_a": 1}))
    task_b: asyncio.Task[dict[str, object]] | None = None
    try:
        await _wait_for_checkpoint_or_task_failure(a_started, task_a)
        task_b = asyncio.create_task(ctx.update_own_config({"draft_b": 2}))
        await _wait_for_checkpoint_or_task_failure(b_started, task_b)

        release_a.set()
        with pytest.raises(RuntimeError, match="write A rejected"):
            await task_a
        assert ctx._effective_config == {"profile": "home", "draft_b": 2}

        release_b.set()
        payload_b = await task_b
    finally:
        release_a.set()
        release_b.set()
        for task in (task_a, task_b):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    assert payload_b["config"] == {"profile": "home", "draft_b": 2}
    assert ctx._effective_config == {"profile": "home", "draft_b": 2}
    assert ctx._effective_config_uncertain is False
    assert instance.refreshed[-1] == {"profile": "home", "draft_b": 2}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_overlapping_persisted_false_preserves_both_updates_and_uncertainty(
    tmp_path: Path,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"profile": "home"}
    ctx._effective_config_uncertain = True

    a_started = asyncio.Event()
    b_started = asyncio.Event()
    release_a = asyncio.Event()
    release_b = asyncio.Event()

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, timeout, wrap_result
        assert request_type == "PLUGIN_CONFIG_UPDATE"
        updates = request_data["updates"]
        assert isinstance(updates, dict)
        if "a" in updates:
            a_started.set()
            await release_a.wait()
            return {"config": {"a": 1}, "persisted": False}
        if "b" in updates:
            b_started.set()
            await release_b.wait()
            return {"config": {"b": 2}, "persisted": False}
        raise AssertionError(f"Unexpected updates {updates}")

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    task_a = asyncio.create_task(ctx.update_own_config({"a": 1}))
    task_b: asyncio.Task[dict[str, object]] | None = None
    try:
        await _wait_for_checkpoint_or_task_failure(a_started, task_a)
        task_b = asyncio.create_task(ctx.update_own_config({"b": 2}))
        await _wait_for_checkpoint_or_task_failure(b_started, task_b)

        release_b.set()
        await task_b
        assert ctx._effective_config == {"profile": "home", "a": 1, "b": 2}
        assert ctx._effective_config_uncertain is True

        release_a.set()
        payload_a = await task_a
    finally:
        release_a.set()
        release_b.set()
        for task in (task_a, task_b):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    assert payload_a["config"] == {"profile": "home", "a": 1, "b": 2}
    assert ctx._effective_config == {"profile": "home", "a": 1, "b": 2}
    assert ctx._effective_config_uncertain is True
    assert instance.refreshed[-1] == {"profile": "home", "a": 1, "b": 2}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_older_read_cannot_overwrite_newer_read_after_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {"profile": "work"}
    ctx._effective_config_uncertain = True

    r1_started = asyncio.Event()
    r2_started = asyncio.Event()
    release_r1 = asyncio.Event()
    release_r2 = asyncio.Event()
    call_count = {"n": 0}

    class _QueryService:
        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            assert plugin_id == "demo"
            call_count["n"] += 1
            if call_count["n"] == 1:
                r1_started.set()
                await release_r1.wait()
                return {"plugin_id": plugin_id, "config": {"profile": "home"}}
            r2_started.set()
            await release_r2.wait()
            return {"plugin_id": plugin_id, "config": {"profile": "work", "persisted": True}}

    monkeypatch.setattr(config_application, "ConfigQueryService", _QueryService)

    read_1 = asyncio.create_task(ctx.get_own_config())
    read_2: asyncio.Task[dict[str, object]] | None = None
    try:
        await _wait_for_checkpoint_or_task_failure(r1_started, read_1)
        read_2 = asyncio.create_task(ctx.get_own_config())
        await _wait_for_checkpoint_or_task_failure(r2_started, read_2)

        release_r2.set()
        res_2 = await read_2
        assert res_2["config"] == {"profile": "work", "persisted": True}
        assert ctx._effective_config == {"profile": "work", "persisted": True}
        assert ctx._effective_config_uncertain is False

        release_r1.set()
        res_1 = await read_1
    finally:
        release_r1.set()
        release_r2.set()
        for task in (read_1, read_2):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    assert res_1["config"] == {"profile": "home"}
    assert ctx._effective_config == {"profile": "work", "persisted": True}
    assert ctx._effective_config_uncertain is False


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ("out_of_order_commits", "later_timeout_earlier_success"))
async def test_context_overlapping_commits_and_timeout_keep_cache_uncertain_until_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {"profile": "home"}

    a_started = asyncio.Event()
    b_started = asyncio.Event()
    release_a = asyncio.Event()
    release_b = asyncio.Event()
    disk_state: dict[str, object] = {}

    class _QueryService:
        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            assert plugin_id == "demo"
            return {"plugin_id": plugin_id, "config": dict(disk_state)}

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, timeout, wrap_result
        assert request_type == "PLUGIN_CONFIG_UPDATE"
        updates = request_data["updates"]
        assert isinstance(updates, dict)
        if scenario == "out_of_order_commits":
            if updates.get("k") == "a":
                a_started.set()
                await release_a.wait()
                return {"config": {"profile": "home", "k": "a"}}
            if updates.get("k") == "b":
                b_started.set()
                await release_b.wait()
                return {"config": {"profile": "home", "k": "b"}}
        else:
            if "a" in updates:
                a_started.set()
                await release_a.wait()
                return {"config": {"profile": "home", "a": 1}}
            if "b" in updates:
                b_started.set()
                await release_b.wait()
                raise TimeoutError("b timed out")
        raise AssertionError(f"Unexpected updates {updates}")

    monkeypatch.setattr(config_application, "ConfigQueryService", _QueryService)
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    if scenario == "out_of_order_commits":
        task_a = asyncio.create_task(ctx.update_own_config({"k": "a"}))
        task_b: asyncio.Task[dict[str, object]] | None = None
        try:
            await _wait_for_checkpoint_or_task_failure(a_started, task_a)
            task_b = asyncio.create_task(ctx.update_own_config({"k": "b"}))
            await _wait_for_checkpoint_or_task_failure(b_started, task_b)

            disk_state = {"profile": "home", "k": "a"}
            release_a.set()
            await task_a
            release_b.set()
            await task_b
        finally:
            release_a.set()
            release_b.set()
            for task in (task_a, task_b):
                if task is not None:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        assert ctx._effective_config_uncertain is True
        reloaded = await ctx.get_own_config()
        assert reloaded["config"] == {"profile": "home", "k": "a"}
        assert ctx._effective_config == {"profile": "home", "k": "a"}
        assert ctx._effective_config_uncertain is False
    else:
        task_a = asyncio.create_task(ctx.update_own_config({"a": 1}))
        task_b = None
        try:
            await _wait_for_checkpoint_or_task_failure(a_started, task_a)
            task_b = asyncio.create_task(ctx.update_own_config({"b": 2}))
            await _wait_for_checkpoint_or_task_failure(b_started, task_b)

            disk_state = {"profile": "home", "a": 1, "b": 2}
            release_b.set()
            timed_out = await task_b
            assert timed_out["persisted"] is None
            assert ctx._effective_config_uncertain is True

            release_a.set()
            await task_a
        finally:
            release_a.set()
            release_b.set()
            for task in (task_a, task_b):
                if task is not None:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        assert ctx._effective_config_uncertain is True
        assert ctx._effective_config == {"profile": "home", "a": 1, "b": 2}
        reloaded = await ctx.get_own_config()
        assert reloaded["config"] == {"profile": "home", "a": 1, "b": 2}
        assert ctx._effective_config == {"profile": "home", "a": 1, "b": 2}
        assert ctx._effective_config_uncertain is False


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("op", ("upsert", "delete", "activate"))
@pytest.mark.parametrize("error_cls", (TimeoutError, asyncio.CancelledError))
async def test_context_profile_write_timeout_or_cancel_invalidates_inflight_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    op: str,
    error_cls: type[BaseException],
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {"profile": "home"}

    read_started = asyncio.Event()
    release_read = asyncio.Event()
    disk_state: dict[str, object] = {"profile": "home"}

    class _QueryService:
        async def get_plugin_effective_config(
            self,
            *,
            plugin_id: str,
            profile_name: str | None = None,
        ) -> dict[str, object]:
            assert plugin_id == "demo"
            assert profile_name is None
            read_started.set()
            await release_read.wait()
            return {"config": {"profile": "home"}}

        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            assert plugin_id == "demo"
            return {"plugin_id": plugin_id, "config": dict(disk_state)}

    async def _failing_profile_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, request_type, request_data, timeout, wrap_result
        raise error_cls("profile write timeout or cancel")

    monkeypatch.setattr(config_application, "ConfigQueryService", _QueryService)
    ctx._send_request_and_wait_async = _failing_profile_rpc  # type: ignore[method-assign]

    slow_read = asyncio.create_task(ctx.get_own_effective_config(None))
    try:
        await _wait_for_checkpoint_or_task_failure(read_started, slow_read)

        with pytest.raises(error_cls):
            if op == "upsert":
                await ctx.upsert_own_profile_config("work", {"k": 1}, make_active=True)
            elif op == "delete":
                await ctx.delete_own_profile_config("work")
            else:
                await ctx.set_own_active_profile("work")

        assert ctx._effective_config_uncertain is True
        disk_state = {"profile": "work"}
        ctx._effective_config = {"profile": "sentinel"}

        release_read.set()
        stale = await slow_read
    finally:
        release_read.set()
        if not slow_read.done():
            slow_read.cancel()
        await asyncio.gather(slow_read, return_exceptions=True)

    assert stale == {"config": {"profile": "home"}}
    assert ctx._effective_config == {"profile": "sentinel"}
    assert ctx._effective_config_uncertain is True

    fresh = await ctx.get_own_config()
    assert fresh["config"] == {"profile": "work"}
    assert ctx._effective_config == {"profile": "work"}
    assert ctx._effective_config_uncertain is False
