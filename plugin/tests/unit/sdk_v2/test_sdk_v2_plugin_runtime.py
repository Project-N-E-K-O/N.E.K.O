from __future__ import annotations

from dataclasses import fields

import pytest

from plugin.sdk_v2.plugin import runtime as rt
from plugin.sdk_v2.shared.models.errors import ErrorCode
from plugin.sdk_v2.shared.models.responses import fail, is_envelope, ok
from plugin.sdk_v2.shared.models.version import SDK_VERSION


class _Ctx:
    plugin_id = "demo"


def test_runtime_constants_types_and_reexports() -> None:
    assert rt.EVENT_META_ATTR == "__neko_event_meta__"
    assert rt.HOOK_META_ATTR == "__neko_hook_meta__"
    assert isinstance(rt.EXTENDED_TYPES, dict)
    assert rt.SDK_VERSION == SDK_VERSION
    assert rt.ErrorCode is ErrorCode
    assert rt.ok is ok
    assert rt.fail is fail
    assert rt.is_envelope is is_envelope


def test_runtime_typed_structures_and_dataclasses() -> None:
    detail: rt.ErrorDetail = {"code": "X", "message": "m", "details": {"k": 1}, "retriable": True}
    ok_env: rt.OkEnvelope = {"success": True, "code": 0, "data": {"v": 1}, "error": None, "time": "t"}
    err_env: rt.ErrEnvelope = {
        "success": False,
        "code": 500,
        "data": None,
        "message": "",
        "error": detail,
        "time": "t",
    }
    env: rt.Envelope = ok_env
    assert env["success"] is True
    env = err_env
    assert env["success"] is False

    em = rt.EventMeta(event_type="entry", id="run")
    assert em.name == ""
    assert em.input_schema is None

    handler = rt.EventHandler(meta=em, handler=lambda: None)
    assert handler.meta.id == "run"

    hm = rt.HookMeta()
    assert hm.target == "*"
    assert hm.timing == "before"
    assert hm.priority == 0
    assert hm.condition is None

    assert [f.name for f in fields(rt.EventMeta)] == ["event_type", "id", "name", "description", "input_schema"]


def test_runtime_error_classes_construct() -> None:
    assert isinstance(rt.PluginConfigError("e"), RuntimeError)
    assert isinstance(rt.PluginCallError("e"), RuntimeError)
    assert isinstance(rt.PluginRouterError("e"), RuntimeError)
    assert isinstance(rt.CircularCallError("e"), RuntimeError)
    assert isinstance(rt.CallChainTooDeepError("e"), RuntimeError)


def test_hook_executor_mixin_not_implemented() -> None:
    mixin = object.__new__(rt.HookExecutorMixin)
    with pytest.raises(NotImplementedError):
        mixin.__init_hook_executor__()


@pytest.mark.asyncio
async def test_plugin_config_contract_methods_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        rt.PluginConfig(_Ctx())

    cfg = object.__new__(rt.PluginConfig)
    with pytest.raises(NotImplementedError):
        await cfg.dump()
    with pytest.raises(NotImplementedError):
        await cfg.get("a.b")
    with pytest.raises(NotImplementedError):
        await cfg.require("a.b")
    with pytest.raises(NotImplementedError):
        await cfg.set("a.b", 1)
    with pytest.raises(NotImplementedError):
        await cfg.update({"a": 1})
    with pytest.raises(NotImplementedError):
        await cfg.get_section("a")


@pytest.mark.asyncio
async def test_plugins_contract_methods_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        rt.Plugins(_Ctx())

    plugins = object.__new__(rt.Plugins)
    with pytest.raises(NotImplementedError):
        await plugins.call_entry("a:b")
    with pytest.raises(NotImplementedError):
        await plugins.call_event("a:e:i")
    with pytest.raises(NotImplementedError):
        await plugins.list()
    with pytest.raises(NotImplementedError):
        await plugins.require("a")


@pytest.mark.asyncio
async def test_router_contract_methods_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        rt.PluginRouter()

    router = object.__new__(rt.PluginRouter)
    with pytest.raises(NotImplementedError):
        await router.add_entry("x", lambda: None)
    with pytest.raises(NotImplementedError):
        await router.remove_entry("x")
    with pytest.raises(NotImplementedError):
        await router.list_entries()


def test_call_chain_helpers_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        rt.get_call_chain()
    with pytest.raises(NotImplementedError):
        rt.get_call_depth()
    with pytest.raises(NotImplementedError):
        rt.is_in_call_chain("p", "e")


@pytest.mark.asyncio
async def test_system_info_contract_methods_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        rt.SystemInfo(_Ctx())

    info = object.__new__(rt.SystemInfo)
    with pytest.raises(NotImplementedError):
        await info.get_system_config()
    with pytest.raises(NotImplementedError):
        info.get_python_env()


@pytest.mark.asyncio
async def test_memory_client_contract_methods_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        rt.MemoryClient(_Ctx())

    mem = object.__new__(rt.MemoryClient)
    with pytest.raises(NotImplementedError):
        await mem.query("b", "q")
    with pytest.raises(NotImplementedError):
        await mem.get("b")


@pytest.mark.asyncio
async def test_store_contract_methods_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        rt.PluginStore()
    with pytest.raises(NotImplementedError):
        rt.PluginKVStore()
    with pytest.raises(NotImplementedError):
        rt.PluginDatabase()
    with pytest.raises(NotImplementedError):
        rt.PluginStatePersistence()

    store = object.__new__(rt.PluginStore)
    with pytest.raises(NotImplementedError):
        await store.get("k")
    with pytest.raises(NotImplementedError):
        await store.set("k", "v")
    with pytest.raises(NotImplementedError):
        await store.delete("k")


def test_runtime_all_exports_exist() -> None:
    for name in rt.__all__:
        assert hasattr(rt, name)

    # Explicit contract placeholders should exist.
    assert rt.CallChain.__name__ == "CallChain"
    assert rt.AsyncCallChain.__name__ == "AsyncCallChain"
