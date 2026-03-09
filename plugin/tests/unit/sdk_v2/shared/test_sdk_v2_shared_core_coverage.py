from __future__ import annotations

from dataclasses import dataclass

import pytest

from plugin.sdk_v2.shared.core import base as core_base
from plugin.sdk_v2.shared.core import config as core_config
from plugin.sdk_v2.shared.core import decorators as core_decorators
from plugin.sdk_v2.shared.core import plugins as core_plugins
from plugin.sdk_v2.shared.core import router as core_router
from plugin.sdk_v2.shared.logging import LogLevel


class _CtxOk:
    plugin_id = "demo"
    logger = None

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"data": {"config": {"feature": {"enabled": True}, "leaf": 1}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> dict[str, object]:
        return {"config": updates}

    async def query_plugins_async(self, filters: dict[str, object], timeout: float = 5.0) -> dict[str, object]:
        return {"plugins": [{"plugin_id": "p"}, "skip"]}

    async def trigger_plugin_event_async(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        return {"ok": True, "target": target_plugin_id, "event_type": event_type, "event_id": event_id, "params": params}


class _CtxNoApis:
    plugin_id = "demo"
    logger = None


class _CtxErrConfig(_CtxNoApis):
    async def get_own_config(self, timeout: float = 5.0) -> object:
        raise RuntimeError("boom")

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> object:
        raise RuntimeError("boom")


class _CtxBadQuery(_CtxNoApis):
    async def query_plugins_async(self, filters: dict[str, object], timeout: float = 5.0) -> object:
        return "bad"


class _CtxBadPlugins(_CtxNoApis):
    async def query_plugins_async(self, filters: dict[str, object], timeout: float = 5.0) -> object:
        return {"plugins": "bad"}


class _CtxQueryRaises(_CtxNoApis):
    async def query_plugins_async(self, filters: dict[str, object], timeout: float = 5.0) -> object:
        raise TimeoutError("boom")


class _CtxCallRaises(_CtxNoApis):
    async def trigger_plugin_event_async(self, **kwargs: object) -> object:
        raise KeyError("boom")


@dataclass(slots=True)
class _RouteRecord:
    handler: object


class _Router:
    def __init__(self, name: str = "router") -> None:
        self._name = name
        self._prefix = ""
        self._entries: dict[str, _RouteRecord] = {}

    def name(self) -> str:
        return self._name

    def set_prefix(self, prefix: str) -> None:
        self._prefix = prefix

    def iter_handlers(self) -> dict[str, object]:
        return {entry_id: record.handler for entry_id, record in self._entries.items()}


class _DemoPlugin(core_base.NekoPluginBase):
    @core_decorators.plugin_entry(id="hello")
    async def hello(self) -> str:
        return "hello"


def test_core_base_additional_logger_branches() -> None:
    base = _DemoPlugin(ctx=_CtxOk())

    assert base.logger_component() == "plugin.demo"
    assert base.logger_component("worker") == "plugin.demo.worker"
    assert base.get_logger("worker") is not None
    logger = base.setup_logger(level=LogLevel.INFO, suffix="worker")
    assert logger is not None
    assert base.sdk_logger is base.logger

    root_logger = base.setup_logger(level=None)
    assert root_logger is base.logger
    assert root_logger is base.sdk_logger

    with pytest.raises(ValueError, match="invalid log level"):
        base.setup_logger(level="wat")
    with pytest.raises(ValueError, match="invalid log_level"):
        base.enable_file_logging(log_level="wat")
    with pytest.raises(ValueError, match="max_bytes must be > 0"):
        base.enable_file_logging(max_bytes=0)
    with pytest.raises(ValueError, match="backup_count must be > 0"):
        base.enable_file_logging(backup_count=0)


def test_core_decorators_plugin_proxy_on_shared_module() -> None:
    sentinel = object()

    def fake_plugin_entry(**kwargs: object):
        assert kwargs == {"id": "x"}
        return sentinel

    original = core_decorators.plugin_entry
    core_decorators.plugin_entry = fake_plugin_entry  # type: ignore[assignment]
    try:
        assert core_decorators.plugin.entry(id="x") is sentinel
    finally:
        core_decorators.plugin_entry = original  # type: ignore[assignment]


def test_core_config_static_helpers() -> None:
    assert core_config.PluginConfig._validate_timeout(1).is_ok()
    assert core_config.PluginConfig._validate_timeout(0).is_err()

    assert core_config.PluginConfig._unwrap_payload("x").is_err()
    assert core_config.PluginConfig._unwrap_payload({"data": {"config": {"x": 1}}}).unwrap() == {"x": 1}
    assert core_config.PluginConfig._unwrap_payload({"config": {"x": 1}}).unwrap() == {"x": 1}
    assert core_config.PluginConfig._unwrap_payload({"config": "x"}).is_err()
    assert core_config.PluginConfig._unwrap_payload({"x": 1}).unwrap() == {"x": 1}

    data = {"a": {"b": 1}}
    assert core_config.PluginConfig._get_by_path(data, "").unwrap() == data
    assert core_config.PluginConfig._get_by_path(data, "a.b").unwrap() == 1
    assert core_config.PluginConfig._get_by_path({"a": 1}, "a.b").is_err()
    assert core_config.PluginConfig._get_by_path(data, "a.c").is_err()

    assert core_config.PluginConfig._set_by_path({"a": 1}, "", {"x": 1}).unwrap() == {"x": 1}
    assert core_config.PluginConfig._set_by_path({"a": 1}, "", 1).is_err()
    assert core_config.PluginConfig._set_by_path({"a": 1}, "a.b", 2).unwrap()["a"]["b"] == 2


@pytest.mark.asyncio
async def test_core_config_error_paths() -> None:
    cfg_err = core_config.PluginConfig(_CtxErrConfig())
    assert (await cfg_err.dump()).is_err()
    assert (await cfg_err.set("x", 1)).is_err()
    assert (await cfg_err.update({"x": 1})).is_err()

    cfg_ok = core_config.PluginConfig(_CtxOk())
    assert (await cfg_ok.dump(timeout=0)).is_err()
    assert (await cfg_ok.get("missing", default=None)).is_err()
    assert (await cfg_ok.get("missing", default=1)).unwrap() == 1
    assert (await cfg_ok.set("", {"root": True})).is_ok()
    assert (await cfg_ok.set("", 1)).is_err()

    class _CtxBadUpdate(_CtxOk):
        async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> object:
            return {"config": "bad"}

    assert (await core_config.PluginConfig(_CtxBadUpdate()).set("x", 1)).is_err()
    assert (await cfg_ok.get_section("leaf")).is_err()


@pytest.mark.asyncio
async def test_core_plugins_error_paths() -> None:
    plugins = core_plugins.Plugins(_CtxNoApis())
    assert (await plugins.list()).is_err()
    assert (await plugins.call(plugin_id="p", event_type="e", event_id="i")).is_err()
    assert (await plugins.list(timeout=0)).is_err()

    assert (await core_plugins.Plugins(_CtxBadQuery()).list()).is_err()
    assert (await core_plugins.Plugins(_CtxBadPlugins()).list()).is_err()
    assert (await core_plugins.Plugins(_CtxQueryRaises()).list()).is_err()
    assert (await core_plugins.Plugins(_CtxCallRaises()).call(plugin_id="p", event_type="e", event_id="i")).is_err()

    ok_plugins = core_plugins.Plugins(_CtxOk())
    listed = await ok_plugins.list()
    assert listed.is_ok()
    assert listed.unwrap() == [{"plugin_id": "p"}]
    assert (await ok_plugins.call_entry("badref")).is_err()
    assert (await ok_plugins.call_event("badref")).is_err()

    class _CtxMissing(_CtxOk):
        async def query_plugins_async(self, filters: dict[str, object], timeout: float = 5.0) -> dict[str, object]:
            return {"plugins": [{"plugin_id": "x"}]}

    assert (await core_plugins.Plugins(_CtxMissing()).require("p")).is_err()


@pytest.mark.asyncio
async def test_core_router_misc_paths() -> None:
    router = core_router.PluginRouter(prefix="p_", name="named")
    assert router.name() == "named"
    assert router._resolve_entry_id("p_run") == "p_run"
    router.set_prefix("x_")
    assert router._resolve_entry_id("run") == "x_run"
    assert router.iter_handlers() == {}
    assert (await router.add_entry("   ", lambda _payload: None)).is_err()
    assert (await router.add_entry("run", lambda _payload: None, input_schema={"type": "object"})).is_ok()
    assert (await router.add_entry("run", lambda _payload: None, replace=True)).is_ok()
    assert (await router.remove_entry("missing")).unwrap() is False


def test_core_base_enable_file_logging_branch(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    base = _DemoPlugin(ctx=_CtxOk())
    calls: dict[str, object] = {}

    monkeypatch.setattr(core_base, "setup_plugin_file_logging", lambda **kwargs: calls.setdefault("kwargs", kwargs) or 123)
    setattr(base, "_file_sink_id", 77)

    logger = base.enable_file_logging(log_dir=tmp_path, max_bytes=10, backup_count=2)
    assert logger is base.file_logger
    assert calls["kwargs"]["previous_sink_id"] == 77


@pytest.mark.asyncio
async def test_core_config_remaining_error_paths() -> None:
    cfg = core_config.PluginConfig(_CtxOk())
    assert (await cfg.get("leaf", timeout=0)).is_err()
    assert (await cfg.require("leaf", timeout=0)).is_err()
    assert (await cfg.set("leaf", 1, timeout=0)).is_err()
    assert (await cfg.update({"x": 1}, timeout=0)).is_err()

    class _CtxUpdateBad(_CtxOk):
        async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> object:
            raise ValueError("bad")

    assert (await core_config.PluginConfig(_CtxUpdateBad()).set("x", 1)).is_err()
    assert (await core_config.PluginConfig(_CtxUpdateBad()).update({"x": 1})).is_err()

    class _CtxDumpBad(_CtxOk):
        async def get_own_config(self, timeout: float = 5.0) -> object:
            return "bad"

    cfg_bad = core_config.PluginConfig(_CtxDumpBad())
    assert (await cfg_bad.get("x")).is_err()
    assert (await cfg_bad.require("x")).is_err()
    assert (await cfg_bad.set("x", 1)).is_err()

    class _CtxSectionBad(_CtxOk):
        async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
            return {"config": {"leaf": 1}}

    assert (await core_config.PluginConfig(_CtxSectionBad()).get_section("leaf", timeout=0)).is_err()


@pytest.mark.asyncio
async def test_core_plugins_remaining_paths() -> None:
    ok_plugins = core_plugins.Plugins(_CtxOk())
    assert (await ok_plugins.call(plugin_id="p", event_type="e", event_id="i", timeout=0)).is_err()

    class _CtxListErr(_CtxNoApis):
        async def query_plugins_async(self, filters: dict[str, object], timeout: float = 5.0) -> object:
            raise ValueError("bad")

    assert (await core_plugins.Plugins(_CtxListErr()).require("p")).is_err()
