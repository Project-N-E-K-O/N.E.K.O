"""Which methods a plugin_entry call resolves to.

Registered entries (collect_entries) and entries the manifest declares are
callable. Private names, SDK base-class methods, the ``entry_<id>`` prefix,
undecorated members of the loader's fallback scan and handlers of other
event types are not. Undeclared public methods still run during the
deprecation grace, with a rate-limited warning.
"""
from __future__ import annotations

import asyncio
import functools
import queue
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from plugin._types.exceptions import PluginEntryNotFoundError, PluginOutputSchemaError
from plugin.core import entry_executor as entry_executor_module
from plugin.core.entry_executor import EntryExecutor
from plugin.core.host import PluginHost, _initial_entry_tables
from plugin.sdk.plugin import (
    NekoPluginBase,
    after_entry,
    around_entry,
    before_entry,
    llm_tool,
    neko_plugin,
    on_event,
    plugin_entry,
    replace_entry,
    timer_interval,
    ui,
)
from plugin.sdk.shared.core.router import PluginRouter

pytestmark = pytest.mark.plugin_unit


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: object, **_kwargs: object) -> None:
        self.warnings.append(message.format(*args) if args else message)

    def error(self, *_args: object, **_kwargs: object) -> None:
        return None

    def info(self, *_args: object, **_kwargs: object) -> None:
        return None

    def debug(self, *_args: object, **_kwargs: object) -> None:
        return None

    def exception(self, *_args: object, **_kwargs: object) -> None:
        return None


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
        # Collected first, as host.py does: a failing collection leaves the maps as they are.
        collected = instance.collect_entries(wrap_with_hooks=True)
        entries.clear()
        metadata.clear()
        events.clear()
        for eid, item in collected.items():
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


class _Params(BaseModel):
    count: int


class _Count(BaseModel):
    count: int


@neko_plugin
class _Dual(NekoPluginBase):
    @plugin_entry(id="dual", params=_Params, strict_output=True)
    async def as_entry(self, params: _Params, **_: Any) -> _Count:
        result: Any = {"count": params.count} if params.count >= 0 else {"count": "not an integer"}
        return result

    @timer_interval(id="dual", seconds=3600, auto_start=False)
    async def as_timer(self, **_: Any) -> str:
        return "timer"


def _dual_through_the_fallback_scan(tmp_path, monkeypatch):
    instance = _Dual(_ctx(tmp_path))

    def broken(**_kwargs: object):
        raise RuntimeError("collect failed")

    monkeypatch.setattr(instance, "collect_entries", broken)
    return _runtime(tmp_path, instance=instance)


def test_an_entry_and_a_timer_sharing_an_id_keep_their_own_maps(tmp_path, monkeypatch):
    executor, instance, _logger = _dual_through_the_fallback_scan(tmp_path, monkeypatch)
    # The id-keyed maps hold the entry with its own metadata; the timer
    # keeps its handler in the per-type map the timer scheduler reads.
    assert executor._entries["dual"] == instance.as_entry
    assert executor._metadata["dual"].event_type == "plugin_entry"
    assert executor._events["timer"]["dual"] == instance.as_timer
    assert _resolve(executor, "dual") == instance.as_entry


async def test_a_shared_id_entry_runs_with_its_own_contract(tmp_path, monkeypatch):
    executor, _instance, _logger = _dual_through_the_fallback_scan(tmp_path, monkeypatch)
    # Its params model builds the argument ...
    assert await executor.invoke("plugin_entry", "dual", {"count": 3}) == {"count": 3}
    # ... and its strict output contract applies.
    with pytest.raises(PluginOutputSchemaError):
        await executor.invoke("plugin_entry", "dual", {"count": -1})


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


class _AddParams(BaseModel):
    n: int


@neko_plugin
class _Declared(NekoPluginBase):
    @plugin_entry(id="add_one", params=_AddParams)
    async def do_add(self, params: _AddParams, **_: Any) -> int:
        return params.n + 1

    @llm_tool(name="lookup", description="look something up")
    async def lookup_tool(self, **_: Any) -> str:
        return "tool"

    @before_entry(target="add_one")
    async def before_hook(self, **_: Any) -> None:
        return None

    @after_entry(target="add_one")
    async def after_hook(self, **_: Any) -> None:
        return None

    @around_entry(target="add_one")
    async def around_hook(self, **_: Any) -> None:
        return None

    @replace_entry(target="nothing")
    async def replace_hook(self, **_: Any) -> None:
        return None

    @ui.context(id="main")
    async def panel_state(self, **_: Any) -> dict:
        return {}


async def test_an_entry_is_reached_by_its_id_not_its_method_name(tmp_path):
    executor, _instance, logger = _runtime(tmp_path, instance=_Declared(_ctx(tmp_path)), manifest=["do_add"])
    assert await executor.invoke("plugin_entry", "add_one", {"n": 1}) == 2
    assert _resolve(executor, "do_add") is None
    assert logger.warnings == []


def test_an_llm_tool_method_is_not_resolved_by_its_name(tmp_path):
    executor, _instance, logger = _runtime(tmp_path, instance=_Declared(_ctx(tmp_path)), manifest=["lookup_tool"])
    assert _resolve(executor, "lookup_tool") is None
    assert logger.warnings == []


@pytest.mark.parametrize("name", ["before_hook", "after_hook", "around_hook", "replace_hook"])
def test_an_entry_hook_is_not_resolved_by_its_name(tmp_path, name):
    executor, _instance, logger = _runtime(tmp_path, instance=_Declared(_ctx(tmp_path)), manifest=[name])
    assert _resolve(executor, name) is None
    assert logger.warnings == []


def test_a_ui_context_provider_is_not_resolved_by_its_name(tmp_path):
    executor, _instance, _logger = _runtime(tmp_path, instance=_Declared(_ctx(tmp_path)))
    assert _resolve(executor, "panel_state") is None


@neko_plugin
class _Shadowed(NekoPluginBase):
    async def refresh(self, **kwargs: Any) -> dict:
        return dict(kwargs)

    @timer_interval(id="refresh", seconds=3600, auto_start=False)
    async def ticker(self, **_: Any) -> str:
        return "timer"


async def test_a_manifest_method_sharing_a_timer_id_runs_without_the_timer_metadata(tmp_path):
    instance = _Shadowed(_ctx(tmp_path))
    executor, _instance, _logger = _runtime(tmp_path, instance=instance, manifest=["refresh"])
    assert executor._metadata["refresh"].event_type == "timer"
    assert executor._call_meta("plugin_entry", "refresh", instance.refresh) is None
    assert executor._call_meta("timer", "refresh", instance.ticker) is executor._metadata["refresh"]
    assert await executor.invoke("plugin_entry", "refresh", {"x": 1}) == {"x": 1}


@neko_plugin
class _Budgets(NekoPluginBase):
    @plugin_entry(id="shared", timeout=0.01)
    async def as_entry(self, **_: Any) -> str:
        return "entry"

    @timer_interval(id="shared", seconds=3600, auto_start=False, metadata={"timeout": 30})
    async def as_timer(self, **_: Any) -> str:
        await asyncio.sleep(0.05)
        return "timer"


async def test_a_handler_sharing_an_id_keeps_its_own_budget(tmp_path, monkeypatch):
    instance = _Budgets(_ctx(tmp_path))

    def broken(**_kwargs: object):
        raise RuntimeError("collect failed")

    # Fallback scan: the id-keyed metadata is the entry's (0.01 s).
    monkeypatch.setattr(instance, "collect_entries", broken)
    executor, _instance, _logger = _runtime(tmp_path, instance=instance)
    assert executor._metadata["shared"].event_type == "plugin_entry"
    timer_meta = executor._call_meta("timer", "shared", instance.as_timer)
    assert timer_meta is not None and timer_meta.event_type == "timer"
    # The timer runs with its own 30 s budget, not the entry's 0.01 s.
    assert await executor.invoke("timer", "shared", {}, registered_only=True) == "timer"
    assert await executor.invoke("plugin_entry", "shared", {}) == "entry"


def test_a_property_is_not_evaluated_by_a_call_to_its_name(tmp_path):
    evaluated: list[str] = []

    async def target(**_: Any) -> str:
        return "ran"

    @neko_plugin
    class WithProperty(NekoPluginBase):
        @property
        def exposed(self):
            evaluated.append("exposed")
            return target

    executor, _instance, _logger = _runtime(tmp_path, instance=WithProperty(_ctx(tmp_path)), manifest=["exposed"])
    # Only what the call does counts (building the tables scans members).
    evaluated.clear()
    assert _resolve(executor, "exposed") is None
    assert evaluated == []


def test_the_narrowing_state_check_does_not_evaluate_a_property(tmp_path):
    evaluated: list[str] = []

    async def target(**_: Any) -> str:
        return "ran"

    @neko_plugin
    class Narrowing(NekoPluginBase):
        # Narrows outside plugin.toml: refused, so the state check runs per call.
        @plugin_entry(id="refused", permissions=["system.env:read"])
        async def refused(self, **_: Any) -> str:
            return "refused"

        @property
        def exposed(self):
            evaluated.append("exposed")
            return target

    instance = Narrowing(_ctx(tmp_path))
    executor, _instance, _logger = _runtime(tmp_path, instance=instance, manifest=["exposed"])
    assert instance._narrowing_refused
    evaluated.clear()
    assert instance._entry_state("exposed") != "closed"
    assert _resolve(executor, "exposed") is None
    assert evaluated == []


def test_partialmethods_and_getattr_names_still_resolve(tmp_path):
    async def _impl(self, value: str, **_: Any) -> str:
        return value

    @neko_plugin
    class Dynamic(NekoPluginBase):
        via_partial = functools.partialmethod(_impl, "partial")

        def __getattr__(self, name: str):
            if name == "via_getattr":
                async def provided(**_: Any) -> str:
                    return "getattr"
                return provided
            raise AttributeError(name)

    instance = Dynamic(_ctx(tmp_path))
    executor, _instance, _logger = _runtime(tmp_path, instance=instance, manifest=["via_partial", "via_getattr"])
    assert _resolve(executor, "via_partial") is not None
    assert _resolve(executor, "via_getattr") is not None


def test_data_descriptors_are_not_evaluated(tmp_path):
    evaluated: list[str] = []

    async def target(**_: Any) -> str:
        return "ran"

    class Descriptor:
        def __get__(self, obj, owner=None):
            evaluated.append("descriptor")
            return target

        def __set__(self, obj, value):
            raise AttributeError("read-only")

    @neko_plugin
    class WithDescriptors(NekoPluginBase):
        data = Descriptor()

        @functools.cached_property
        def cached(self):
            evaluated.append("cached")
            return target

    instance = WithDescriptors(_ctx(tmp_path))
    executor, _instance, _logger = _runtime(tmp_path, instance=instance, manifest=["data", "cached"])
    # Building the tables scans members (and caches the cached property):
    # only what the call does counts.
    instance.__dict__.pop("cached", None)
    evaluated.clear()
    assert _resolve(executor, "data") is None
    assert _resolve(executor, "cached") is None
    assert evaluated == []


async def test_a_failing_collection_reports_an_unknown_id_as_not_found(tmp_path, monkeypatch):
    instance = _Plugin(_ctx(tmp_path))

    def broken(**_kwargs: object):
        raise RuntimeError("collect failed")

    # Fallback-scan mode: every refresh raises the plugin's collection error.
    monkeypatch.setattr(instance, "collect_entries", broken)
    executor, _instance, _logger = _runtime(tmp_path, instance=instance)
    with pytest.raises(PluginEntryNotFoundError):
        await executor.invoke("plugin_entry", "no_such_entry", {})
    # Logged (error type only), so a broken collection is told apart from a typo.
    assert [w for w in _logger.warnings if "collection failed" in w] == [
        "Plugin entry collection failed while resolving entry='no_such_entry': error_type=RuntimeError"
    ]
    assert await executor.invoke("plugin_entry", "registered", {}) == "registered"


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
