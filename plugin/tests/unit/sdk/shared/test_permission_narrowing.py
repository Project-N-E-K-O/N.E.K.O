"""Component permission narrowing: decorators, registration, routers, hooks, host scan, CLI parity."""

from __future__ import annotations

import asyncio
import queue
from pathlib import Path

import pytest

from plugin._types.entry_metadata import ENTRY_PROJECTION_SHAPED_FIELDS, unhandled_entry_contract_fields
from plugin.core import registry
from plugin.core.state import state
from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir
from plugin.sdk.plugin import (
    NekoPluginBase,
    after_entry,
    around_entry,
    before_entry,
    custom_event,
    hook,
    lifecycle,
    llm_tool,
    neko_plugin,
    on_event,
    plugin,
    plugin_entry,
    replace_entry,
    timer_interval,
)
from plugin.sdk.plugin.base import _dynamic_entry_registration, _entry_listing
from plugin.sdk.plugin.llm_tool import LLM_TOOL_META_ATTR
from plugin.sdk.shared.constants import EVENT_META_ATTR, HOOK_META_ATTR
from plugin.sdk.shared.core.router import PluginRouter
from plugin.sdk.shared.models import Err, Ok
from plugin.sdk.shared.models.exceptions import PermissionDeniedError
from plugin.sdk.shared.permissions.narrowing import (
    Component,
    EntryHook,
    merge_narrowing,
    resolve_component_permissions,
)

NET = "system.network:connect"
FS = "system.fs:read"
ENV = "system.env:read"
DECLARED_TOML = (
    '[plugin]\nid = "narrow_demo"\n'
    f'[permissions.require]\n"{NET}" = {{ reason = "x" }}\n'
    f'[permissions.ask]\n"{FS}" = {{ reason = "y" }}\n'
)


class _Logger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, message: str, *args: object) -> None:
        self.errors.append(message.format(*args))

    def warning(self, *_args: object, **_kwargs: object) -> None: ...

    def info(self, *_args: object, **_kwargs: object) -> None: ...

    def debug(self, *_args: object, **_kwargs: object) -> None: ...

    def exception(self, *_args: object, **_kwargs: object) -> None: ...


def _ctx(tmp_path: Path, toml: str | None = DECLARED_TOML):
    from plugin.core.context import PluginContext

    config_path = tmp_path / "plugin.toml"
    if toml is not None:
        config_path.write_text(toml, encoding="utf-8")
    return PluginContext(
        plugin_id="narrow_demo",
        config_path=config_path,
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=queue.Queue(),
        message_queue=queue.Queue(),
    )


def _meta_permissions(fn: object) -> object:
    for attr in (EVENT_META_ATTR, HOOK_META_ATTR, LLM_TOOL_META_ATTR):
        meta = getattr(fn, attr, None)
        if meta is not None:
            return meta.permissions
    raise AssertionError("no metadata")


# --- every component decorator takes permissions= ----------------------------------------


COMPONENT_DECORATORS = {
    "plugin_entry": lambda **kw: plugin_entry(id="e", **kw),
    "plugin.entry": lambda **kw: plugin.entry(id="e", **kw),
    "llm_tool": lambda **kw: llm_tool(name="t", **kw),
    "hook": lambda **kw: hook(target="e", **kw),
    "plugin.hook": lambda **kw: plugin.hook(target="e", **kw),
    "before_entry": lambda **kw: before_entry(target="e", **kw),
    "after_entry": lambda **kw: after_entry(target="e", **kw),
    "around_entry": lambda **kw: around_entry(target="e", **kw),
    "replace_entry": lambda **kw: replace_entry(target="e", **kw),
    "on_event": lambda **kw: on_event(event_type="x", id="e", **kw),
    "plugin.event": lambda **kw: plugin.event(event_type="x", id="e", **kw),
    "custom_event": lambda **kw: custom_event(event_type="x", id="e", **kw),
    "plugin.custom_event": lambda **kw: plugin.custom_event(event_type="x", id="e", **kw),
    "timer_interval": lambda **kw: timer_interval(id="e", seconds=5, **kw),
    "plugin.timer": lambda **kw: plugin.timer(id="e", seconds=5, **kw),
}


@pytest.mark.parametrize("name", sorted(COMPONENT_DECORATORS))
def test_every_component_decorator_records_its_narrowing(name):
    make = COMPONENT_DECORATORS[name]

    async def handler(self, **_): ...

    assert _meta_permissions(make(permissions=[NET, FS, NET])(handler)) == (NET, FS)

    async def plain(self, **_): ...

    # Left out: inherits everything, recorded as None.
    assert _meta_permissions(make()(plain)) is None

    async def bad(self, **_): ...

    with pytest.raises(TypeError, match="permissions= must be a list"):
        make(permissions=NET)(bad)


def test_non_component_decorators_still_reject_permissions():
    with pytest.raises(TypeError):
        lifecycle(id="startup", permissions=[NET])  # type: ignore[call-arg]


# --- registration in the plugin process ---------------------------------------------------


@neko_plugin
class _NarrowedPlugin(NekoPluginBase):
    @plugin_entry(id="fits", permissions=[NET])
    async def fits(self, **_):
        return Ok({})

    @plugin_entry(id="outside", permissions=[ENV])
    async def outside(self, **_):
        return Ok({})

    @plugin_entry(id="inherits")
    async def inherits(self, **_):
        return Ok({})

    @timer_interval(id="tick_ok", seconds=60, permissions=[FS])
    async def tick_ok(self, **_): ...

    @timer_interval(id="tick_bad", seconds=60, permissions=[ENV])
    async def tick_bad(self, **_): ...

    @on_event(event_type="custom_x", id="sub_bad", permissions=["memory.read"])
    async def sub_bad(self, **_): ...


def test_registration_keeps_fitting_components_and_drops_the_others(tmp_path):
    ctx = _ctx(tmp_path)
    instance = _NarrowedPlugin(ctx)
    entries = instance.collect_entries()
    assert set(entries) == {"fits", "inherits", "tick_ok"}
    assert entries["fits"].meta.permissions == (NET,)
    assert entries["inherits"].meta.permissions is None
    assert entries["tick_ok"].meta.permissions == (FS,)
    errors = ctx.logger.errors
    assert any("entry 'outside'" in message and f'"{ENV}"' in message for message in errors)
    # An event handler that fails is not subscribed.
    assert any("timer handler 'tick_bad'" in message for message in errors)
    assert any("custom_x handler 'sub_bad'" in message and "unknown permission" in message for message in errors)
    # Logged once, however often the entries are collected.
    count = len(errors)
    instance.collect_entries()
    instance.list_entries()
    assert len(ctx.logger.errors) == count


def test_registration_without_a_permissions_table_refuses_any_narrowing(tmp_path):
    ctx = _ctx(tmp_path, '[plugin]\nid = "narrow_demo"\n')
    entries = _NarrowedPlugin(ctx).collect_entries()
    assert set(entries) == {"inherits"}


def test_an_unknown_declaration_set_checks_nothing(tmp_path):
    # [permissions] that is not a table: what is declared is unknown.
    ctx = _ctx(tmp_path, 'permissions = "x"\n[plugin]\nid = "narrow_demo"\n')
    entries = _NarrowedPlugin(ctx).collect_entries()
    assert {"fits", "outside", "inherits", "tick_ok", "tick_bad", "sub_bad"} <= set(entries)


def test_decorator_metadata_is_not_mutated_by_registration(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        @plugin_entry(id="a", permissions=[NET])
        async def a(self, **_): ...

        @before_entry(target="a", permissions=[FS])
        async def guard(self, **_): ...

    entries = P(_ctx(tmp_path)).collect_entries()
    assert entries["a"].meta.permissions == (NET, FS)
    assert getattr(P.a, EVENT_META_ATTR).permissions == (NET,)


def test_plugins_without_narrowing_are_returned_untouched(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        @plugin_entry(id="a")
        async def a(self, **_): ...

        @timer_interval(id="t", seconds=5)
        async def t(self, **_): ...

        @before_entry(target="a")
        async def guard(self, **_): ...

    entries = P(_ctx(tmp_path)).collect_entries()
    # The decorator's own metadata objects: nothing copied or rewritten.
    assert entries["a"].meta is getattr(P.a, EVENT_META_ATTR)
    assert entries["t"].meta is getattr(P.t, EVENT_META_ATTR)


# --- entry hooks ------------------------------------------------------------------------------


def test_entry_hook_permissions_join_the_entries_it_targets(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        @plugin_entry(id="a", permissions=[NET])
        async def a(self, **_): ...

        @plugin_entry(id="b", permissions=[])
        async def b(self, **_): ...

        @timer_interval(id="t", seconds=5, permissions=[])
        async def t(self, **_): ...

        @before_entry(target="a", permissions=[FS])
        async def only_a(self, **_): ...

        @after_entry(target="*", permissions=[NET])
        async def every(self, **_): ...

    entries = P(_ctx(tmp_path)).collect_entries()
    assert entries["a"].meta.permissions == (NET, FS)
    assert entries["b"].meta.permissions == (NET,)
    # Entry hooks attach to entries only, not to event handlers.
    assert entries["t"].meta.permissions == ()


def test_an_entry_hook_with_an_undeclared_permission_closes_its_targets(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        @plugin_entry(id="a", permissions=[NET])
        async def a(self, **_): ...

        @plugin_entry(id="b")
        async def b(self, **_): ...

        @around_entry(target="a", permissions=[ENV])
        async def wrap_a(self, **_): ...

    ctx = _ctx(tmp_path)
    entries = P(ctx).collect_entries()
    assert set(entries) == {"b"}
    assert any("entry 'a' is closed by around hook 'wrap_a'" in message for message in ctx.logger.errors)


def test_an_entry_hook_without_narrowing_makes_its_target_inherit_everything(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        @plugin_entry(id="a", permissions=[NET])
        async def a(self, **_): ...

        @replace_entry(target="a")
        async def swap(self, **_): ...

    assert P(_ctx(tmp_path)).collect_entries()["a"].meta.permissions is None


def test_merge_rules():
    assert merge_narrowing((NET,), [(FS,), (NET,)]) == (NET, FS)
    assert merge_narrowing((NET,), [None]) is None
    assert merge_narrowing(None, [(FS,)]) is None
    resolved = resolve_component_permissions(
        {"x": Component("entry 'x'", "plugin_entry", "x", ())},
        [EntryHook("before hook 'h'", "*", ("bogus",))],
        frozenset({NET}),
    )
    assert not resolved["x"].registered


# --- id conflicts: permissions follow the winning handler ------------------------------------------


class _ClashRouter(PluginRouter):
    @plugin_entry(id="shared", permissions=[ENV])
    async def shared(self, payload):
        return Ok({})

    @plugin_entry(id="router_only", permissions=[FS])
    async def router_only(self, payload):
        return Ok({})


def test_id_conflict_permissions_follow_the_winner(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        @plugin_entry(id="shared", permissions=[NET])
        async def shared(self, **_): ...

    ctx = _ctx(tmp_path)
    instance = P(ctx)
    instance.include_router(_ClashRouter())
    entries = instance.collect_entries()
    # The plugin class wins the clash and its narrowing comes with it; the
    # router's losing handler does not take the id down.
    assert entries["shared"].meta.permissions == (NET,)
    assert entries["shared"].handler == instance.shared
    assert entries["router_only"].meta.permissions == (FS,)

    class Later(PluginRouter):
        @plugin_entry(id="router_only", permissions=[NET])
        async def router_only(self, payload):
            return Ok({})

    instance.include_router(Later())
    # A later router wins over an earlier one, permissions included.
    assert instance.collect_entries()["router_only"].meta.permissions == (NET,)


def test_a_plugin_entry_wins_over_an_event_handler_with_its_permissions(tmp_path):
    class R(PluginRouter):
        @plugin_entry(id="dual", permissions=[FS])
        async def dual(self, payload):
            return Ok({})

    @neko_plugin
    class P(NekoPluginBase):
        @timer_interval(id="dual", seconds=5, permissions=[NET])
        async def dual(self, **_): ...

    instance = P(_ctx(tmp_path))
    instance.include_router(R())
    entries = instance.collect_entries()
    assert entries["dual"].meta.event_type == "plugin_entry"
    assert entries["dual"].meta.permissions == (FS,)


# --- routers ------------------------------------------------------------------------------------


def test_router_entries_inherit_the_router_narrowing(tmp_path):
    class R(PluginRouter):
        permissions = [FS]

        @plugin_entry(id="plain")
        async def plain(self, payload):
            return Ok({})

        @plugin_entry(id="own", permissions=[NET])
        async def own(self, payload):
            return Ok({})

    @neko_plugin
    class P(NekoPluginBase):
        pass

    instance = P(_ctx(tmp_path))
    router = R()
    instance.include_router(router)
    entries = instance.collect_entries()
    assert entries["plain"].meta.permissions == (FS,)
    assert entries["own"].meta.permissions == (NET,)


def test_include_router_checks_each_entry(tmp_path):
    class R(PluginRouter):
        @plugin_entry(id="bad", permissions=[ENV])
        async def bad(self, payload):
            return Ok({})

        @plugin_entry(id="good", permissions=[FS])
        async def good(self, payload):
            return Ok({})

    @neko_plugin
    class P(NekoPluginBase):
        pass

    ctx = _ctx(tmp_path)
    instance = P(ctx)
    instance.include_router(R(), prefix="p_")
    # Checked when included: the refusal is logged right away.
    assert any("entry 'p_bad'" in message for message in ctx.logger.errors)
    logged = len(ctx.logger.errors)
    entries = instance.collect_entries()
    assert "p_bad" not in entries and entries["p_good"].meta.permissions == (FS,)
    assert len(ctx.logger.errors) == logged

    # A router-level narrowing outside plugin.toml refuses the entries inheriting it.
    class Inheriting(PluginRouter):
        @plugin_entry(id="inh")
        async def inh(self, payload):
            return Ok({})

        @plugin_entry(id="own", permissions=[NET])
        async def own(self, payload):
            return Ok({})

    instance.include_router(Inheriting(permissions=[ENV]))
    entries = instance.collect_entries()
    assert "inh" not in entries and entries["own"].meta.permissions == (NET,)


def test_add_entry_checks_on_a_bound_router(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        pass

    ctx = _ctx(tmp_path)
    instance = P(ctx)
    router = PluginRouter(name="dyn", permissions=[FS])
    instance.include_router(router)

    async def handler(payload):
        return Ok({})

    result = asyncio.run(router.add_entry("bad", handler, permissions=[ENV]))
    assert isinstance(result, Err)
    assert isinstance(result.error, PermissionDeniedError)
    assert result.error.reason == "not_declared"
    assert "bad" not in router.entry_ids
    assert any("Entry not registered" in message for message in ctx.logger.errors)

    assert isinstance(asyncio.run(router.add_entry("own", handler, permissions=[NET])), Ok)
    assert isinstance(asyncio.run(router.add_entry("inherit", handler)), Ok)
    entries = instance.collect_entries()
    assert entries["own"].meta.permissions == (NET,)
    assert entries["inherit"].meta.permissions == (FS,)


def test_add_entry_on_an_unbound_router_is_checked_when_included(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        pass

    router = PluginRouter(name="later")

    async def handler(payload):
        return Ok({})

    assert isinstance(asyncio.run(router.add_entry("bad", handler, permissions=[ENV])), Ok)
    ctx = _ctx(tmp_path)
    instance = P(ctx)
    instance.include_router(router)
    assert "bad" not in instance.collect_entries()
    assert any("entry 'bad'" in message for message in ctx.logger.errors)


def test_add_entry_rejects_a_malformed_narrowing():
    async def handler(payload):
        return Ok({})

    result = asyncio.run(PluginRouter().add_entry("x", handler, permissions="system.fs:read"))  # type: ignore[arg-type]
    assert isinstance(result, Err)


# --- LLM tools and dynamic entries -------------------------------------------------------------------


def test_llm_tool_narrowing_is_checked_and_recorded(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        @llm_tool(name="good", permissions=[NET])
        async def good(self, **_):
            return "ok"

        @llm_tool(name="bad", permissions=[ENV])
        async def bad(self, **_):
            return "no"

    ctx = _ctx(tmp_path)
    instance = P(ctx)
    assert [tool["name"] for tool in instance.list_llm_tools()] == ["good"]
    entries = instance.collect_entries()
    assert entries["__llm_tool__good"].meta.permissions == (NET,)
    assert "__llm_tool__bad" not in entries
    assert any("__llm_tool__bad" in message for message in ctx.logger.errors)
    sent = []
    while not ctx.message_queue.empty():
        sent.append(ctx.message_queue.get_nowait())
    registrations = [msg for msg in sent if msg.get("type") == "ENTRY_UPDATE"]
    assert [msg["meta"].get("permissions") for msg in registrations] == [(NET,)]
    with pytest.raises(PermissionDeniedError):
        instance.register_llm_tool(name="late", description="", parameters=None, handler=lambda **_: 1, permissions=[ENV])
    assert instance.register_llm_tool(
        name="late", description="", parameters=None, handler=lambda **_: 1, permissions=[FS]
    )


def test_register_dynamic_entry_checks_narrowing(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        pass

    instance = P(_ctx(tmp_path))
    with pytest.raises(PermissionDeniedError):
        instance.register_dynamic_entry("x", lambda **_: 1, permissions=[ENV])
    assert "x" not in instance.collect_entries()
    assert instance.register_dynamic_entry("x", lambda **_: 1, permissions=[FS])
    assert instance.collect_entries()["x"].meta.permissions == (FS,)


def test_projections_carry_permissions_only_when_set():
    from plugin.sdk.shared.core.events import EventMeta

    plain = EventMeta(event_type="plugin_entry", id="a")
    narrowed = EventMeta(event_type="plugin_entry", id="a", permissions=(NET,))
    assert "permissions" not in _entry_listing("a", plain, enabled=True, dynamic=False)
    assert _entry_listing("a", narrowed, enabled=True, dynamic=False)["permissions"] == (NET,)
    assert "permissions" not in _dynamic_entry_registration("p", "a", plain)["meta"]
    assert unhandled_entry_contract_fields(
        EventMeta(event_type="plugin_entry", id="a", permissions=()), ENTRY_PROJECTION_SHAPED_FIELDS
    ) == {"permissions": ()}


# --- host static scan agrees with the plugin process --------------------------------------------------


class _HostRouter(PluginRouter):
    permissions = [FS]

    @plugin_entry(id="r_inherit")
    async def r_inherit(self, payload):
        return Ok({})

    @plugin_entry(id="r_bad", permissions=[ENV])
    async def r_bad(self, payload):
        return Ok({})


@neko_plugin
class _HostPlugin(NekoPluginBase):
    __routers__ = [_HostRouter]

    @plugin_entry(id="fits", permissions=[NET])
    async def fits(self, **_): ...

    @plugin_entry(id="outside", permissions=[ENV])
    async def outside(self, **_): ...

    @plugin_entry(id="hooked", permissions=[])
    async def hooked(self, **_): ...

    @before_entry(target="hooked", permissions=[NET])
    async def guard(self, **_): ...

    @timer_interval(id="tick_bad", seconds=5, permissions=[ENV])
    async def tick_bad(self, **_): ...


def _host_scan(pid: str, conf: dict, manifest_path: Path) -> dict[str, object]:
    try:
        registry.scan_static_metadata(pid, _HostPlugin, conf, conf.get("plugin", {}), manifest_path=manifest_path)
        with state.acquire_event_handlers_read_lock():
            return {
                key[len(pid) + 1:]: handler.meta
                for key, handler in state.event_handlers.items()
                if key.startswith(f"{pid}.")
            } | {
                key: handler.meta for key, handler in state.event_handlers.items() if key == f"{pid}:timer:tick_bad"
            }
    finally:
        with state.acquire_event_handlers_write_lock():
            for key in [key for key in state.event_handlers if key.startswith((f"{pid}.", f"{pid}:"))]:
                del state.event_handlers[key]


def test_host_scan_and_plugin_process_register_the_same_entries(tmp_path):
    import tomllib

    ctx = _ctx(tmp_path)
    # The effective configuration carries no [permissions] table: the host
    # reads the declarations from the manifest file, as the plugin process does.
    conf = {key: value for key, value in tomllib.loads(DECLARED_TOML).items() if key != "permissions"}
    host = _host_scan("narrow_host_demo", conf, ctx.config_path)
    instance = _HostPlugin(ctx)
    # As lifekit does: the __routers__ classes are included at runtime.
    instance.include_router(_HostRouter())
    process = instance.collect_entries()
    process_entries = {eid: item.meta for eid, item in process.items() if item.meta.event_type == "plugin_entry"}
    assert sorted(host) == sorted(process_entries) == ["fits", "hooked", "r_inherit"]
    for eid, meta in process_entries.items():
        assert host[eid].permissions == meta.permissions
    assert host["hooked"].permissions == (NET,)
    assert host["r_inherit"].permissions == (FS,)
    # The refused event handler is neither in the host table nor subscribed.
    assert "tick_bad" not in process

    previews = {
        item["id"]: item
        for item in registry._extract_entries_preview(
            "narrow_host_demo", _HostPlugin, conf, {}, manifest_path=ctx.config_path
        )
    }
    assert sorted(previews) == ["fits", "hooked", "r_inherit"]
    assert previews["fits"]["permissions"] == (NET,)
    assert "permissions" not in registry._extract_entries_preview(
        "narrow_host_demo", type("Q", (), {"a": plugin_entry(id="a")(lambda self: None)}), conf, {}
    )[0]


def test_manifest_entry_tables_cannot_set_permissions():
    declared = registry._normalized_entry_declaration({"id": "a", "permissions": [ENV], "timeout": 3})
    assert "permissions" not in declared


# --- neko-plugin check and registration reach the same verdict ------------------------------------------


PARITY_CASES = [
    ("@plugin_entry(id='a', permissions=['system.network:connect'])", True),
    ("@plugin_entry(id='a', permissions=['system.env:read'])", False),
    ("@plugin_entry(id='a', permissions=['memory.read'])", False),
    ("@plugin_entry(id='a', permissions=[])", True),
    ("@llm_tool(name='a', permissions=['system.fs:read'])", True),
    ("@llm_tool(name='a', permissions=['system.process:spawn'])", False),
    ("@timer_interval(id='a', seconds=5, permissions=['system.fs:read'])", True),
    ("@timer_interval(id='a', seconds=5, permissions=['system.env:read'])", False),
    ("@on_event(event_type='x', id='a', permissions=['system.network:connect'])", True),
    ("@custom_event(event_type='x', id='a', permissions=['system.env:read'])", False),
    ("@plugin.entry(id='a', permissions=['system.fs:read'])", True),
    ("@plugin.timer(id='a', seconds=5, permissions=['system.env:read'])", False),
    # The default spelled out.
    ("@plugin_entry(id='a', permissions=None)", True),
]


@pytest.mark.parametrize("decorator,accepted", PARITY_CASES)
@pytest.mark.parametrize("toml_tail", ["", "no_table"])
def test_check_and_registration_agree(tmp_path, decorator, accepted, toml_tail):
    toml = DECLARED_TOML if toml_tail == "" else '[plugin]\nid = "narrow_demo"\n'
    if toml_tail == "no_table":
        # Without a table nothing is declared: any non-empty narrowing fails.
        accepted = "permissions=[]" in decorator or "permissions=None" in decorator
    source = (
        "from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin, plugin_entry, llm_tool, "
        "timer_interval, on_event, custom_event\n"
        "@neko_plugin\n"
        "class Demo(NekoPluginBase):\n"
        f"    {decorator}\n"
        "    async def a(self, **_):\n"
        "        return 1\n"
    )
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text(toml, encoding="utf-8")
    (plugin_dir / "worker.py").write_text(source, encoding="utf-8")
    static_errors = [
        # Labels carry the decorated function's line (5); every narrowing message has it.
        message for level, message in validate_plugin_dir(plugin_dir) if level == "error" and "worker.py:5" in message
    ]

    namespace: dict[str, object] = {}
    exec(compile(source, str(plugin_dir / "worker.py"), "exec"), namespace)  # noqa: S102 - synthetic test source
    instance = namespace["Demo"](_ctx(plugin_dir, None))  # type: ignore[operator]
    registered = {eid for eid in instance.collect_entries() if eid in ("a", "__llm_tool__a")}

    assert (not static_errors) is accepted
    assert bool(registered) is accepted


# --- review fixes ---------------------------------------------------------------------------------


def test_a_plugin_whose_components_were_all_refused_is_marked_for_the_host_loader(tmp_path):
    # The plugin process loader falls back to scanning methods only when
    # collect_entries() returned nothing; this flag stops that fallback from
    # bringing refused components back (plugin/core/host.py).
    @neko_plugin
    class P(NekoPluginBase):
        @plugin_entry(id="only", permissions=[ENV])
        async def only(self, **_): ...

    instance = P(_ctx(tmp_path))
    assert instance.collect_entries() == {}
    assert instance._narrowing_refused == {("plugin_entry", "only")}

    @neko_plugin
    class Empty(NekoPluginBase):
        pass

    empty = Empty(_ctx(tmp_path))
    assert empty.collect_entries() == {}
    assert not getattr(empty, "_narrowing_refused", None)


@pytest.mark.parametrize("refused_wins", [True, False])
def test_duplicate_ids_in_one_class_decide_on_the_winner_on_both_sides(tmp_path, refused_wins):
    import tomllib

    # getmembers order is by name: "z_..." is collected last and wins the id.
    ok_perm, bad_perm = [NET], [ENV]
    winner, loser = (bad_perm, ok_perm) if refused_wins else (ok_perm, bad_perm)

    @neko_plugin
    class P(NekoPluginBase):
        @plugin_entry(id="x1", permissions=loser)
        async def a_first(self, **_): ...

        @plugin_entry(id="x1", permissions=winner)
        async def z_last(self, **_): ...

    ctx = _ctx(tmp_path)
    conf = tomllib.loads(DECLARED_TOML)
    pid = "narrow_dup_demo"
    try:
        registry.scan_static_metadata(pid, P, conf, {}, manifest_path=ctx.config_path)
        with state.acquire_event_handlers_read_lock():
            host = {key for key in state.event_handlers if key.startswith(f"{pid}.")}
    finally:
        with state.acquire_event_handlers_write_lock():
            for key in [key for key in state.event_handlers if key.startswith((f"{pid}.", f"{pid}:"))]:
                del state.event_handlers[key]
    process = P(ctx).collect_entries()
    if refused_wins:
        assert host == set() and process == {}
    else:
        assert host == {f"{pid}.x1"} and set(process) == {"x1"}
        assert process["x1"].meta.permissions == (NET,)


def test_entry_hooks_join_dynamic_entries_and_llm_tools(tmp_path):
    @neko_plugin
    class P(NekoPluginBase):
        @before_entry(target="*", permissions=[FS])
        async def every(self, **_): ...

        @llm_tool(name="tool", permissions=[NET])
        async def tool(self, **_):
            return 1

    instance = P(_ctx(tmp_path))
    assert instance.collect_entries()["__llm_tool__tool"].meta.permissions == (NET, FS)
    instance.register_dynamic_entry("dyn", lambda **_: 1, permissions=[])
    assert instance.collect_entries()["dyn"].meta.permissions == (FS,)

    @neko_plugin
    class Closing(NekoPluginBase):
        @before_entry(target="dyn", permissions=[ENV])
        async def bad(self, **_): ...

    closing = Closing(_ctx(tmp_path))
    with pytest.raises(PermissionDeniedError) as caught:
        closing.register_dynamic_entry("dyn", lambda **_: 1)
    assert caught.value.permission == ENV
    assert "dyn" not in closing.collect_entries()
    # Other ids are not targeted by that hook.
    assert closing.register_dynamic_entry("other", lambda **_: 1)
    assert closing.collect_entries()["other"].meta.permissions is None

