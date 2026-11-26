from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

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
@pytest.mark.parametrize("first_get_fails", (False, True))
async def test_context_concurrent_profile_activates_are_serialized(
    tmp_path: Path,
    first_get_fails: bool,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    home_activate_started = asyncio.Event()
    release_home_activate = asyncio.Event()
    first_get_started = asyncio.Event()
    release_first_get = asyncio.Event()
    active_profile = {"name": "home"}
    rpc_trace: list[tuple[str, str]] = []

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
            target = str(request_data["profile_name"])
            rpc_trace.append(("activate", target))
            if target == "home":
                home_activate_started.set()
                await release_home_activate.wait()
            active_profile["name"] = target
            return {"config_profiles": {"active": target}}
        if request_type == "PLUGIN_CONFIG_GET":
            current = active_profile["name"]
            rpc_trace.append(("get", current))
            if current == "home" and not first_get_started.is_set():
                first_get_started.set()
                await release_first_get.wait()
                if first_get_fails:
                    raise RuntimeError("stale refresh failed")
                return {"config": {"profile": "home"}}
            return {"config": {"profile": "work"}}
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    first_activate = asyncio.create_task(ctx.set_own_active_profile("home"))
    second_activate: asyncio.Task[dict[str, object]] | None = None
    try:
        await _wait_for_checkpoint_or_task_failure(home_activate_started, first_activate)

        second_activate = asyncio.create_task(ctx.set_own_active_profile("work"))
        await asyncio.sleep(0)
        # Second profile activation must wait while the first activate RPC holds the write lock.
        assert rpc_trace == [("activate", "home")]

        release_home_activate.set()
        await _wait_for_checkpoint_or_task_failure(first_get_started, first_activate)

        # Once home finishes its write RPC and moves post-lock refresh out of the lock,
        # work proceeds promptly and finishes without waiting for the stale home refresh.
        await second_activate
        assert ctx._effective_config == {"profile": "work"}
        assert ctx._effective_config_uncertain is False

        release_first_get.set()
        await first_activate
    finally:
        release_home_activate.set()
        release_first_get.set()
        for task in (first_activate, second_activate):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    assert rpc_trace == [("activate", "home"), ("get", "home"), ("activate", "work"), ("get", "work")]
    assert ctx._effective_config == {"profile": "work"}
    assert ctx._effective_config_uncertain is False


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_outcome", ("stale_return", "raise_error"))
async def test_context_external_config_update_during_profile_refresh_is_not_overwritten_or_cleared(
    tmp_path: Path,
    refresh_outcome: str,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    get_started = asyncio.Event()
    release_get = asyncio.Event()

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
        assert request_type == "PLUGIN_CONFIG_GET"
        get_started.set()
        await release_get.wait()
        if refresh_outcome == "raise_error":
            raise RuntimeError("refresh read failed")
        return {"config": {"profile": "stale_work"}}

    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    refresh_task = asyncio.create_task(ctx._refresh_effective_config_after_profile_change(timeout=5.0))
    try:
        await _wait_for_checkpoint_or_task_failure(get_started, refresh_task)

        # Simulate external host CONFIG_UPDATE replacing cache while refresh GET is in flight.
        ctx._set_effective_config_cache({"profile": "pushed"})

        release_get.set()
        await refresh_task
    finally:
        release_get.set()
        if not refresh_task.done():
            refresh_task.cancel()
        await asyncio.gather(refresh_task, return_exceptions=True)

    assert ctx._effective_config == {"profile": "pushed"}
    assert ctx._effective_config_uncertain is False


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("update", "replace"))
async def test_context_concurrent_writes_and_profile_activate_are_serialized(
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

    a_started = asyncio.Event()
    release_a = asyncio.Event()
    work_get_started = asyncio.Event()
    release_work_get = asyncio.Event()
    events: list[str] = []

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
        if request_type in {"PLUGIN_CONFIG_UPDATE", "PLUGIN_CONFIG_REPLACE"}:
            payload = request_data.get("updates") if request_type == "PLUGIN_CONFIG_UPDATE" else request_data.get("config")
            assert isinstance(payload, dict)
            if payload.get("tag") == "a":
                events.append("write_a")
                a_started.set()
                await release_a.wait()
                raise RuntimeError("a rejected")
            if payload.get("tag") == "b":
                events.append("write_b")
                raise RuntimeError("b rejected")
        if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
            events.append("activate_work")
            return {"config_profiles": {"active": "work"}}
        if request_type == "PLUGIN_CONFIG_GET":
            events.append("get_work")
            work_get_started.set()
            await release_work_get.wait()
            return {"config": {"profile": "work"}}
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    # Phase 1: write A fails while profile activation queues behind it; then work loads cleanly.
    task_a = asyncio.create_task(
        ctx.update_own_config({"tag": "a"})
        if operation == "update"
        else ctx.replace_own_config({"tag": "a"})
    )
    task_activate: asyncio.Task[dict[str, object]] | None = None
    task_b: asyncio.Task[dict[str, object]] | None = None
    try:
        await _wait_for_checkpoint_or_task_failure(a_started, task_a)
        task_activate = asyncio.create_task(ctx.set_own_active_profile("work"))
        await asyncio.sleep(0)
        assert events == ["write_a"]

        release_a.set()
        with pytest.raises(RuntimeError, match="a rejected"):
            await task_a

        # Phase 2: post-lock refresh runs outside the write lock, so failing write B proceeds
        # without waiting on a slow refresh GET and never pushes an empty {} runtime config.
        await _wait_for_checkpoint_or_task_failure(work_get_started, task_activate)
        refreshed_before_b = len(instance.refreshed)
        task_b = asyncio.create_task(
            ctx.update_own_config({"tag": "b"})
            if operation == "update"
            else ctx.replace_own_config({"tag": "b"})
        )
        with pytest.raises(RuntimeError, match="b rejected"):
            await task_b
        assert len(instance.refreshed) == refreshed_before_b
        assert ctx._effective_config is None

        release_work_get.set()
        await task_activate
    finally:
        release_a.set()
        release_work_get.set()
        for task in (task_a, task_activate, task_b):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    assert events == ["write_a", "activate_work", "get_work", "write_b"]
    # The earlier refresh GET started before write B bumped generation, so it stays ignored;
    # the subsequent get_own_config cleanly reloads active profile "work".
    assert ctx._effective_config is None
    reloaded = await ctx.get_own_config()
    assert reloaded["config"] == {"profile": "work"}
    assert ctx._effective_config == {"profile": "work"}
    assert ctx._effective_config_uncertain is False
    assert instance.refreshed[-1] == {"profile": "work"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_three_serialized_writes_a_save_b_save_c_fail_keeps_b_saved(
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
    release_a = asyncio.Event()
    b_started = asyncio.Event()
    release_b = asyncio.Event()
    c_started = asyncio.Event()
    release_c = asyncio.Event()

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
            return {"config": {"profile": "home", "a": 1}}
        if "plugin" in updates:
            b_started.set()
            await release_b.wait()
            return {"config": {"profile": "home", "a": 1, "plugin": {"store": {"enabled": True}}}}
        if "c" in updates:
            c_started.set()
            await release_c.wait()
            raise RuntimeError("write C rejected")
        raise AssertionError(f"Unexpected updates {updates}")

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    task_a = asyncio.create_task(ctx.update_own_config({"a": 1}))
    task_b: asyncio.Task[dict[str, object]] | None = None
    task_c: asyncio.Task[dict[str, object]] | None = None
    try:
        await _wait_for_checkpoint_or_task_failure(a_started, task_a)
        task_b = asyncio.create_task(ctx.update_own_config({"plugin": {"store": {"enabled": True}}}))
        task_c = asyncio.create_task(ctx.update_own_config({"c": 3}))
        await asyncio.sleep(0)
        assert not b_started.is_set()
        assert not c_started.is_set()

        release_a.set()
        await task_a

        await _wait_for_checkpoint_or_task_failure(b_started, task_b)
        assert not c_started.is_set()
        release_b.set()
        await task_b

        await _wait_for_checkpoint_or_task_failure(c_started, task_c)
        assert ctx._effective_config == {
            "profile": "home",
            "a": 1,
            "plugin": {"store": {"enabled": True}},
            "c": 3,
        }
        release_c.set()
        with pytest.raises(RuntimeError, match="write C rejected"):
            await task_c
    finally:
        release_a.set()
        release_b.set()
        release_c.set()
        for task in (task_a, task_b, task_c):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    expected = {"profile": "home", "a": 1, "plugin": {"store": {"enabled": True}}}
    assert ctx._effective_config == expected
    assert ctx._effective_config_uncertain is False
    assert instance.refreshed[-1] == expected


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_serialized_overlapping_failed_writes_restore_original_config(
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
            raise RuntimeError("write B rejected")
        raise AssertionError(f"Unexpected updates {updates}")

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    task_a = asyncio.create_task(ctx.update_own_config({"draft_a": 1}))
    task_b: asyncio.Task[dict[str, object]] | None = None
    try:
        await _wait_for_checkpoint_or_task_failure(a_started, task_a)
        assert ctx._effective_config == {"profile": "home", "draft_a": 1}

        task_b = asyncio.create_task(ctx.update_own_config({"draft_b": 2}))
        await asyncio.sleep(0)
        assert not b_started.is_set()

        release_a.set()
        with pytest.raises(RuntimeError, match="write A rejected"):
            await task_a

        await _wait_for_checkpoint_or_task_failure(b_started, task_b)
        assert ctx._effective_config == {"profile": "home", "draft_b": 2}

        release_b.set()
        with pytest.raises(RuntimeError, match="write B rejected"):
            await task_b
    finally:
        release_a.set()
        release_b.set()
        for task in (task_a, task_b):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    assert ctx._effective_config == {"profile": "home"}
    assert ctx._effective_config_uncertain is False
    assert instance.refreshed[-1] == {"profile": "home"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_serialized_persisted_false_and_rejected_writes(
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
            raise RuntimeError("write A rejected")
        return {"config": dict(updates), "persisted": False}

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    res_a, res_b = await asyncio.gather(
        ctx.update_own_config({"draft_a": 1}),
        ctx.update_own_config({"draft_b": 2}),
        return_exceptions=True,
    )
    assert isinstance(res_a, RuntimeError)
    assert isinstance(res_b, dict)
    assert res_b["config"] == {"profile": "home", "draft_b": 2}
    assert ctx._effective_config == {"profile": "home", "draft_b": 2}
    assert ctx._effective_config_uncertain is False

    # Overlapping persisted=False writes from uncertain state preserve both keys and uncertainty.
    ctx._effective_config = {"profile": "home"}
    ctx._effective_config_uncertain = True
    res_1, res_2 = await asyncio.gather(
        ctx.update_own_config({"a": 1}),
        ctx.update_own_config({"b": 2}),
    )
    assert res_1["config"] == {"profile": "home", "a": 1}
    assert res_2["config"] == {"profile": "home", "a": 1, "b": 2}
    assert ctx._effective_config == {"profile": "home", "a": 1, "b": 2}
    assert ctx._effective_config_uncertain is True
    assert instance.refreshed[-1] == {"profile": "home", "a": 1, "b": 2}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("update", "replace"))
@pytest.mark.parametrize("outcome", ("fail", "succeed"))
async def test_context_external_config_update_during_write_prevents_stale_rollback_and_overwrite(
    tmp_path: Path,
    operation: str,
    outcome: str,
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

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, request_data, timeout, wrap_result
        assert request_type in {"PLUGIN_CONFIG_UPDATE", "PLUGIN_CONFIG_REPLACE"}
        write_started.set()
        await release_write.wait()
        if outcome == "fail":
            raise RuntimeError("rejected")
        return {"config": {"profile": "home", "draft": 1}}

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    pending_write = asyncio.create_task(
        ctx.update_own_config({"draft": 1})
        if operation == "update"
        else ctx.replace_own_config({"profile": "home", "draft": 1})
    )
    try:
        await _wait_for_checkpoint_or_task_failure(write_started, pending_write)

        # Simulate external host.py CONFIG_UPDATE installing authoritative config mid-write.
        ctx._set_effective_config_cache({"profile": "pushed"})

        release_write.set()
        if outcome == "fail":
            with pytest.raises(RuntimeError, match="rejected"):
                await pending_write
        else:
            payload = await pending_write
            assert payload == {"config": {"profile": "home", "draft": 1}}
    finally:
        release_write.set()
        if not pending_write.done():
            pending_write.cancel()
        await asyncio.gather(pending_write, return_exceptions=True)

    assert ctx._effective_config == {"profile": "pushed"}
    assert ctx._effective_config_uncertain is (outcome == "succeed")
    assert instance.refreshed[-1] == {"profile": "pushed"}


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
@pytest.mark.parametrize(
    ("op", "expected_rpc", "target_profile", "final_disk_config"),
    [
        ("upsert", "PLUGIN_CONFIG_PROFILE_UPSERT", "work", {"profile": "work"}),
        ("delete", "PLUGIN_CONFIG_PROFILE_DELETE", "home", {"profile": "default"}),
        ("activate", "PLUGIN_CONFIG_PROFILE_ACTIVATE", "work", {"profile": "work"}),
    ],
)
@pytest.mark.parametrize("error_cls", (TimeoutError, asyncio.CancelledError))
async def test_context_profile_write_timeout_or_cancel_invalidates_inflight_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    op: str,
    expected_rpc: str,
    target_profile: str,
    final_disk_config: dict[str, object],
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
    received: list[tuple[str, object]] = []

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
        del method_name, timeout, wrap_result
        received.append((request_type, request_data.get("profile_name")))
        raise error_cls("profile write timeout or cancel")

    monkeypatch.setattr(config_application, "ConfigQueryService", _QueryService)
    ctx._send_request_and_wait_async = _failing_profile_rpc  # type: ignore[method-assign]

    slow_read = asyncio.create_task(ctx.get_own_effective_config(None))
    try:
        await _wait_for_checkpoint_or_task_failure(read_started, slow_read)

        with pytest.raises(error_cls):
            if op == "upsert":
                await ctx.upsert_own_profile_config(target_profile, {"k": 1}, make_active=True)
            elif op == "delete":
                await ctx.delete_own_profile_config(target_profile)
            else:
                await ctx.set_own_active_profile(target_profile)

        assert received == [(expected_rpc, target_profile)]
        assert ctx._effective_config_uncertain is True
        disk_state = dict(final_disk_config)
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
    assert fresh["config"] == final_disk_config
    assert ctx._effective_config == final_disk_config
    assert ctx._effective_config_uncertain is False


@pytest.mark.plugin_unit
def test_context_cross_loop_and_cross_thread_config_writes_serialize_cleanly(
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
    ctx._effective_config = {"count": 0}

    state_mu = threading.Lock()
    server_config: dict[str, object] = {"count": 0}
    in_flight = 0
    max_in_flight = 0
    order: list[str] = []

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        nonlocal in_flight, max_in_flight
        del method_name, timeout, wrap_result
        assert request_type == "PLUGIN_CONFIG_UPDATE"
        updates = request_data["updates"]
        assert isinstance(updates, dict)
        with state_mu:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            tag = next(iter(updates.keys()))
        try:
            await asyncio.sleep(0.05 if tag == "timer" else 0.01)
            with state_mu:
                server_config.update(updates)
                order.append(tag)
                return {"config": dict(server_config), "persisted": True}
        finally:
            with state_mu:
                in_flight -= 1

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    async def _contend_on_loop(tag_a: str, tag_b: str) -> tuple[dict[str, object], dict[str, object]]:
        return await asyncio.gather(
            ctx.update_own_config({tag_a: 1}),
            ctx.update_own_config({tag_b: 2}),
        )

    # Phase 1: contention on loop 1 followed by contention on a brand-new loop 2 must not hit loop-binding errors.
    asyncio.run(_contend_on_loop("loop1_a", "loop1_b"))
    asyncio.run(_contend_on_loop("loop2_a", "loop2_b"))
    assert max_in_flight == 1

    # Phase 2: timer thread running asyncio.run concurrent with command-loop updates must serialize and finish promptly.
    timer_entered = threading.Event()
    orig_rpc = ctx._send_request_and_wait_async

    async def _threaded_rpc(**kwargs: object) -> dict[str, object]:
        request_data = kwargs["request_data"]
        assert isinstance(request_data, dict)
        updates = request_data["updates"]
        if isinstance(updates, dict) and "timer" in updates:
            timer_entered.set()
        return await orig_rpc(**kwargs)  # type: ignore[misc]

    ctx._send_request_and_wait_async = _threaded_rpc  # type: ignore[method-assign]

    thread_errors: list[BaseException] = []
    thread_results: list[dict[str, object]] = []

    def _timer_worker() -> None:
        try:
            thread_results.append(asyncio.run(ctx.update_own_config({"timer": True}, timeout=2.0)))
        except BaseException as exc:  # pragma: no cover - defensive capture
            thread_errors.append(exc)

    t = threading.Thread(target=_timer_worker, daemon=True)
    started = time.monotonic()
    t.start()
    assert timer_entered.wait(timeout=1.0), "timer thread did not start config write"

    async def _command_loop_writer() -> dict[str, object]:
        return await ctx.update_own_config({"command": True}, timeout=2.0)

    cmd_result = asyncio.run(_command_loop_writer())
    t.join(timeout=2.0)
    elapsed = time.monotonic() - started

    assert not t.is_alive()
    assert not thread_errors
    assert len(thread_results) == 1
    assert elapsed < 1.0
    assert max_in_flight == 1
    assert order[-2:] == ["timer", "command"]
    assert cmd_result["config"] == server_config
    assert ctx._effective_config == server_config
    assert ctx._effective_config_uncertain is False
    assert instance.refreshed[-1] == server_config


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("waiter_op", ("update", "replace", "upsert", "delete", "activate"))
async def test_context_queued_config_writes_respect_timeout_and_preserve_state(
    tmp_path: Path,
    waiter_op: str,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance
    ctx._effective_config = {"profile": "home", "keep": 1}

    holder_started = asyncio.Event()
    release_holder = asyncio.Event()
    rpc_calls: list[tuple[str, float]] = []

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        del method_name, wrap_result
        rpc_calls.append((request_type, timeout))
        if request_type == "PLUGIN_CONFIG_REPLACE" and request_data.get("config") == {"holder": True}:
            holder_started.set()
            await release_holder.wait()
            return {"config": {"profile": "home", "keep": 1, "holder": True}}
        if request_type == "PLUGIN_CONFIG_UPDATE":
            updates = request_data["updates"]
            assert isinstance(updates, dict)
            return {"config": {"profile": "home", "keep": 1, "holder": True, **updates}}
        if request_type == "PLUGIN_CONFIG_REPLACE":
            return {"config": dict(request_data["config"])}  # type: ignore[arg-type]
        if request_type == "PLUGIN_CONFIG_PROFILE_UPSERT":
            return {"config": {"x": 1}, "affected_active": False}
        if request_type == "PLUGIN_CONFIG_PROFILE_DELETE":
            return {"removed": True, "affected_active": False}
        if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
            return {"config_profiles": {"active": "work"}}
        if request_type == "PLUGIN_CONFIG_GET":
            return {"config": {"profile": "work"}}
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    async def _call_waiter(wait_timeout: float) -> object:
        if waiter_op == "update":
            return await ctx.update_own_config({"queued": 1}, timeout=wait_timeout)
        if waiter_op == "replace":
            return await ctx.replace_own_config({"queued": 1}, timeout=wait_timeout)
        if waiter_op == "upsert":
            return await ctx.upsert_own_profile_config("work", {"queued": 1}, timeout=wait_timeout)
        if waiter_op == "delete":
            return await ctx.delete_own_profile_config("work", timeout=wait_timeout)
        return await ctx.set_own_active_profile("work", timeout=wait_timeout)

    holder = asyncio.create_task(ctx.replace_own_config({"holder": True}, timeout=5.0))
    try:
        await _wait_for_checkpoint_or_task_failure(holder_started, holder)
        refreshed_while_holding = list(instance.refreshed)

        started = time.monotonic()
        if waiter_op == "update":
            timed_out_res = await _call_waiter(0.05)
            assert isinstance(timed_out_res, dict)
            assert timed_out_res["success"] is False
            assert timed_out_res["persisted"] is False
            assert timed_out_res["config"] == {"holder": True}
        else:
            with pytest.raises(TimeoutError):
                await _call_waiter(0.05)
        elapsed = time.monotonic() - started
        assert elapsed < 0.3

        # No extra RPC was sent, runtime cache stays on holder's optimistic state, and write flag remains intact.
        assert rpc_calls == [("PLUGIN_CONFIG_REPLACE", pytest.approx(5.0, abs=0.1))]
        assert ctx._effective_config == {"holder": True}
        assert ctx._effective_config_uncertain is False
        assert ctx._config_write_in_progress is True
        assert instance.refreshed == refreshed_while_holding

        release_holder.set()
        await holder
    finally:
        release_holder.set()
        if not holder.done():
            holder.cancel()
        await asyncio.gather(holder, return_exceptions=True)

    assert ctx._config_write_in_progress is False
    assert ctx._effective_config == {"profile": "home", "keep": 1, "holder": True}

    # Verify remaining timeout budget after queuing is passed to the subsequent RPC.
    rpc_calls.clear()
    holder_started.clear()
    release_holder.clear()

    async def _timed_holder() -> dict[str, object]:
        return await ctx.replace_own_config({"holder": True}, timeout=5.0)

    holder2 = asyncio.create_task(_timed_holder())
    waiter2: asyncio.Task[object] | None = None
    try:
        await _wait_for_checkpoint_or_task_failure(holder_started, holder2)
        waiter2 = asyncio.create_task(_call_waiter(0.4))
        await asyncio.sleep(0.08)
        release_holder.set()
        await holder2
        await waiter2
    finally:
        release_holder.set()
        for t in (holder2, waiter2):
            if t is not None:
                if not t.done():
                    t.cancel()
                await asyncio.gather(t, return_exceptions=True)

    assert len(rpc_calls) >= 2
    assert 0.0 < rpc_calls[1][1] < 0.35


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("op", ("update", "replace", "upsert", "delete", "activate"))
@pytest.mark.parametrize("bad_timeout", (None, "bad", float("nan"), float("inf"), True))
async def test_context_invalid_timeout_fails_before_mutating_cache_or_write_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    op: str,
    bad_timeout: object,
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
    rpc_called = False

    async def _unexpected_rpc(**_kwargs: object) -> dict[str, object]:
        nonlocal rpc_called
        rpc_called = True
        return {}

    ctx._send_request_and_wait_async = _unexpected_rpc  # type: ignore[method-assign]

    with pytest.raises((TypeError, ValueError)):
        if op == "update":
            await ctx.update_own_config({"x": 1}, timeout=bad_timeout)  # type: ignore[arg-type]
        elif op == "replace":
            await ctx.replace_own_config({"x": 1}, timeout=bad_timeout)  # type: ignore[arg-type]
        elif op == "upsert":
            await ctx.upsert_own_profile_config("work", {"x": 1}, timeout=bad_timeout)  # type: ignore[arg-type]
        elif op == "delete":
            await ctx.delete_own_profile_config("work", timeout=bad_timeout)  # type: ignore[arg-type]
        else:
            await ctx.set_own_active_profile("work", timeout=bad_timeout)  # type: ignore[arg-type]

    assert rpc_called is False
    assert ctx._effective_config == {"profile": "home"}
    assert ctx._effective_config_uncertain is False
    assert getattr(ctx, "_config_write_in_progress", False) is False
    assert instance.refreshed == []

    # Subsequent reads must still populate cache normally.
    ctx._effective_config = None

    class _QueryService:
        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            assert plugin_id == "demo"
            return {"plugin_id": plugin_id, "config": {"profile": "reloaded"}}

    monkeypatch.setattr(config_application, "ConfigQueryService", _QueryService)
    reloaded = await ctx.get_own_config()
    assert reloaded["config"] == {"profile": "reloaded"}
    assert ctx._effective_config == {"profile": "reloaded"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("write_op", ("update", "replace"))
async def test_context_failed_write_after_failed_profile_refresh_does_not_push_empty_config(
    tmp_path: Path,
    write_op: str,
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
            return {"config_profiles": {"active": "work"}}
        if request_type == "PLUGIN_CONFIG_GET":
            raise RuntimeError("refresh read failed")
        if request_type in {"PLUGIN_CONFIG_UPDATE", "PLUGIN_CONFIG_REPLACE"}:
            raise RuntimeError("write rejected")
        raise AssertionError(f"Unexpected RPC {request_type}")

    ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    await ctx.set_own_active_profile("work")
    assert ctx._effective_config is None
    assert instance.refreshed == []

    with pytest.raises(RuntimeError, match="write rejected"):
        if write_op == "update":
            await ctx.update_own_config({"x": 1})
        else:
            await ctx.replace_own_config({"x": 1})

    assert ctx._effective_config is None
    assert instance.refreshed == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("write_op", ("update", "replace"))
async def test_context_successful_persisted_write_clears_prior_uncertain_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_op: str,
) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._effective_config = {"k": 0}

    rpc_calls = 0

    async def _fake_rpc(
        *,
        method_name: str,
        request_type: str,
        request_data: dict[str, object],
        timeout: float,
        wrap_result: bool,
    ) -> dict[str, object]:
        nonlocal rpc_calls
        del method_name, request_data, timeout, wrap_result
        rpc_calls += 1
        if rpc_calls == 1:
            raise TimeoutError("timed out")
        return {"config": {"k": 2}, "persisted": True}

    ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

    timed_out = await ctx.update_own_config({"k": 1})
    assert timed_out["persisted"] is None
    assert ctx._effective_config_uncertain is True

    if write_op == "update":
        committed = await ctx.update_own_config({"k": 2})
    else:
        committed = await ctx.replace_own_config({"k": 2})

    assert committed["config"] == {"k": 2}
    assert ctx._effective_config == {"k": 2}
    assert ctx._effective_config_uncertain is False

    class _UnexpectedQueryService:
        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            raise AssertionError(f"Unexpected remote read for {plugin_id}")

    monkeypatch.setattr(config_application, "ConfigQueryService", _UnexpectedQueryService)
    cached = await ctx.get_own_config()
    assert cached["config"] == {"k": 2}
