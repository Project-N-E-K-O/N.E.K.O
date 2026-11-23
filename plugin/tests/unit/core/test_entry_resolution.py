"""Which methods a plugin_entry call resolves to.

Registered entries (collect_entries) and entries the manifest declares are
callable. Private names, SDK base-class methods, the ``entry_<id>`` prefix,
undecorated members of the loader's fallback scan and handlers of other
event types are not. Undeclared public methods still run during the
deprecation grace, with a rate-limited warning.
"""
from __future__ import annotations

import queue
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plugin._types.exceptions import PluginEntryNotFoundError
from plugin.core import entry_executor as entry_executor_module
from plugin.core.entry_executor import EntryExecutor
from plugin.core.host import PluginHost, _initial_entry_tables
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, on_event, plugin_entry, timer_interval
from plugin.sdk.shared.core.router import PluginRouter

pytestmark = pytest.mark.plugin_unit


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: object, **_kwargs: object) -> None:
        self.warnings.append(message.format(*args) if args else message)

    def error(self, *_args: object, **_kwargs: object) -> None: ...
    def info(self, *_args: object, **_kwargs: object) -> None: ...
    def debug(self, *_args: object, **_kwargs: object) -> None: ...
    def exception(self, *_args: object, **_kwargs: object) -> None: ...


@neko_plugin
class _Plugin(NekoPluginBase):
    @plugin_entry(id="registered")
    async def registered(self, **_: Any) -> str:
        return "registered"

    async def declared(self, **_: Any) -> str:
        return "declared"

    async def legacy(self, **_: Any) -> str:
        return "legacy"

    async def _private(self, **_: Any) -> str:
        return "private"

    async def entry_prefixed(self, **_: Any) -> str:
        return "prefixed"

    @timer_interval(id="tick", seconds=3600, auto_start=False)
    async def tick(self, **_: Any) -> str:
        return "tick"

    @timer_interval(id="other_tick", seconds=3600, auto_start=False)
    async def ticker(self, **_: Any) -> str:
        return "ticker"

    @on_event(event_type="custom_x", id="ping")
    async def ping(self, **_: Any) -> str:
        return "ping"

    label = "a truthy, non-callable attribute"

    async def run_update(self, **_: Any) -> str:
        # Overrides an SDK base-class method: still not an entry.
        return "override"


def _ctx(tmp_path: Path):
    from plugin.core.context import PluginContext

    config_path = tmp_path / "plugin.toml"
    config_path.write_text('[plugin]\nid = "resolution_demo"\n', encoding="utf-8")
    return PluginContext(
        plugin_id="resolution_demo",
        config_path=config_path,
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=queue.Queue(),
        message_queue=queue.Queue(),
    )


def _runtime(tmp_path: Path, *, manifest: list[str] | None = None, instance: Any = None):
    """An executor over the tables the plugin process builds (host.py)."""
    ctx = _ctx(tmp_path)
    instance = instance or _Plugin(ctx)
    entries, metadata, events = _initial_entry_tables(instance, ctx.logger)

    def refresh() -> None:
        entries.clear()
        metadata.clear()
        events.clear()
        for eid, item in instance.collect_entries(wrap_with_hooks=True).items():
            entries[eid] = item.handler
            metadata[eid] = item.meta
            events.setdefault(getattr(item.meta, "event_type", "plugin_entry"), {})[eid] = item.handler

    logger = _Logger()
    context = ctx
    context._handler_scope = lambda _label: nullcontext()  # type: ignore[method-assign]
    context._run_scope = lambda _run: nullcontext()  # type: ignore[method-assign]
    context._lanlan_scope = lambda _name: nullcontext()  # type: ignore[method-assign]
    executor = EntryExecutor(
        plugin_id="resolution_demo", context=context, instance=instance, entries=entries,
        metadata=metadata, events=events, default_timeout=5.0, refresh=refresh,
        should_persist=lambda _method: False, persist=None, logger=logger,
        manifest_entry_ids=manifest,
    )
    return executor, instance, logger


def _resolve(executor: EntryExecutor, entry_id: str):
    return executor._resolve("plugin_entry", entry_id, registered_only=False)


# --- first level: never resolved ---------------------------------------------------------------


def test_a_private_name_is_not_resolved(tmp_path):
    executor, _instance, _logger = _runtime(tmp_path, manifest=["_private"])
    assert _resolve(executor, "_private") is None


def test_an_sdk_base_class_method_is_not_resolved(tmp_path):
    executor, _instance, logger = _runtime(tmp_path, manifest=["export_push"])
    # Defined by the SDK base only; declared in the manifest or not.
    assert _resolve(executor, "export_push") is None
    assert _resolve(executor, "finish") is None
    # Overridden by the plugin: still an SDK method name.
    assert _resolve(executor, "run_update") is None
    assert logger.warnings == []


def test_the_entry_prefix_is_not_an_alias(tmp_path):
    executor, instance, _logger = _runtime(tmp_path, manifest=["prefixed"])
    assert _resolve(executor, "prefixed") is None
    # The method under its own (undeclared) name is the grace case.
    assert _resolve(executor, "entry_prefixed") == instance.entry_prefixed


def test_the_fallback_scan_maps_only_members_with_event_metadata(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    instance = _Plugin(ctx)

    def broken(**_kwargs: object):
        raise RuntimeError("collect failed")

    monkeypatch.setattr(instance, "collect_entries", broken)
    entry_map, meta_map, events = _initial_entry_tables(instance, ctx.logger)
    assert set(entry_map) == {"registered", "tick", "other_tick", "ping"}
    assert set(meta_map) == set(entry_map)
    assert "registered" in events["plugin_entry"]


def test_a_handler_of_another_event_type_is_not_a_plugin_entry(tmp_path):
    executor, _instance, _logger = _runtime(tmp_path, manifest=["tick", "ticker", "ping"])
    # By its id (the maps hold it, typed as a timer) ...
    assert "tick" in executor._entries
    assert _resolve(executor, "tick") is None
    assert _resolve(executor, "other_tick") is None
    # ... and by its method name (attribute fallback).
    assert _resolve(executor, "ticker") is None
    assert _resolve(executor, "ping") is None
    # The custom-event path still reaches its own handler.
    assert executor._resolve("custom_x", "ping", registered_only=True) is not None


def test_an_entry_and_a_timer_sharing_an_id_resolve_to_the_entry(tmp_path, monkeypatch):
    @neko_plugin
    class Dual(NekoPluginBase):
        @plugin_entry(id="dual")
        async def as_entry(self, **_: Any) -> str:
            return "entry"

        @timer_interval(id="dual", seconds=3600, auto_start=False)
        async def as_timer(self, **_: Any) -> str:
            return "timer"

    ctx = _ctx(tmp_path)
    instance = Dual(ctx)

    def broken(**_kwargs: object):
        raise RuntimeError("collect failed")

    # The loader's fallback scan keeps both; the id map holds the timer.
    monkeypatch.setattr(instance, "collect_entries", broken)
    executor, _instance, _logger = _runtime(tmp_path, instance=instance)
    assert executor._metadata["dual"].event_type == "timer"
    assert _resolve(executor, "dual") == instance.as_entry


def test_a_wrapped_handler_of_another_event_type_is_not_a_plugin_entry(tmp_path):
    async def tick(self, **_: Any) -> str:
        return "tick"

    timer = timer_interval(id="wrapped_tick", seconds=3600, auto_start=False)(tick)

    async def wrapper(self, **kwargs: Any) -> str:
        return await timer(self, **kwargs)

    # Only __wrapped__ carries the metadata.
    wrapper.__wrapped__ = timer  # type: ignore[attr-defined]

    @neko_plugin
    class Wrapped(NekoPluginBase):
        guarded = wrapper

    executor, _instance, _logger = _runtime(tmp_path, instance=Wrapped(_ctx(tmp_path)), manifest=["guarded"])
    assert _resolve(executor, "guarded") is None


def test_a_non_callable_attribute_is_not_resolved(tmp_path):
    executor, _instance, _logger = _runtime(tmp_path, manifest=["label"])
    assert _resolve(executor, "label") is None


async def test_an_unresolved_call_is_entry_not_found(tmp_path):
    executor, _instance, _logger = _runtime(tmp_path)
    for entry_id in ("_private", "export_push", "prefixed", "tick", "ticker"):
        with pytest.raises(PluginEntryNotFoundError):
            await executor.invoke("plugin_entry", entry_id, {})


# --- declared entries ----------------------------------------------------------------------------


async def test_registered_and_manifest_entries_run_without_a_warning(tmp_path):
    executor, _instance, logger = _runtime(tmp_path, manifest=["declared"])
    assert await executor.invoke("plugin_entry", "registered", {}) == "registered"
    assert await executor.invoke("plugin_entry", "declared", {}) == "declared"
    assert logger.warnings == []


async def test_router_and_dynamic_entries_run_including_reserved_ids(tmp_path):
    class Router(PluginRouter):
        @plugin_entry(id="routed")
        async def routed(self, **_: Any) -> str:
            return "routed"

    instance = _Plugin(_ctx(tmp_path))
    instance.include_router(Router(name="r"))

    async def tool(**_: Any) -> str:
        return "tool"

    # LLM tools register under a reserved, underscore-prefixed id.
    assert instance.register_dynamic_entry("__llm_tool__probe", tool)
    executor, _instance, logger = _runtime(tmp_path, instance=instance)
    assert await executor.invoke("plugin_entry", "routed", {}) == "routed"
    assert await executor.invoke("plugin_entry", "__llm_tool__probe", {}) == "tool"
    assert logger.warnings == []


def test_without_manifest_ids_plain_methods_fall_under_the_grace(tmp_path):
    executor, instance, logger = _runtime(tmp_path, manifest=None)
    assert _resolve(executor, "declared") == instance.declared
    assert len(logger.warnings) == 1


def test_a_registered_entry_takes_the_fast_path(tmp_path):
    executor, instance, _logger = _runtime(tmp_path)
    calls: list[str] = []
    instance._entry_state = lambda entry_id: calls.append(entry_id) or "unchecked"  # type: ignore[method-assign]
    assert _resolve(executor, "registered") is not None
    assert calls == []


# --- second level: deprecation grace ------------------------------------------------------------


async def test_an_undeclared_public_method_still_runs_with_one_warning(tmp_path):
    executor, _instance, logger = _runtime(tmp_path, manifest=[])
    assert await executor.invoke("plugin_entry", "legacy", {"secret_arg": "value-123"}) == "legacy"
    assert await executor.invoke("plugin_entry", "legacy", {"secret_arg": "value-123"}) == "legacy"
    assert len(logger.warnings) == 1
    message = logger.warnings[0]
    assert "'legacy'" in message and "next" in message
    assert "value-123" not in message and "secret_arg" not in message


def test_the_warning_is_limited_per_id(tmp_path):
    executor, _instance, logger = _runtime(tmp_path)
    _resolve(executor, "legacy")
    _resolve(executor, "entry_prefixed")
    _resolve(executor, "legacy")
    assert len(logger.warnings) == 2


def test_without_the_grace_an_undeclared_method_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(entry_executor_module, "UNDECLARED_ENTRY_GRACE", False)
    executor, instance, logger = _runtime(tmp_path, manifest=["declared"])
    assert _resolve(executor, "legacy") is None
    assert _resolve(executor, "declared") == instance.declared
    assert logger.warnings == []


# --- the host sends the manifest's ids -----------------------------------------------------------


def test_the_host_puts_the_manifest_ids_in_the_launch_options():
    # Unbound call: constructing a real host opens its transport.
    host = SimpleNamespace(_startup_options={"startup_failure": "warn"})
    PluginHost.set_manifest_entry_ids(host, ["declared", 3])  # type: ignore[arg-type, list-item]
    assert host._startup_options["manifest_entry_ids"] == ["declared", "3"]
