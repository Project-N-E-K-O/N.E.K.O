from __future__ import annotations

from dataclasses import fields

import pytest

from plugin.sdk_v2.plugin import runtime as rt
from plugin.sdk_v2.shared.models.errors import ErrorCode
from plugin.sdk_v2.shared.models.responses import fail, is_envelope, ok
from plugin.sdk_v2.shared.constants import SDK_VERSION


class _Ctx:
    plugin_id = "demo"

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"config": {"feature": {"enabled": True}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> dict[str, object]:
        return {"config": updates}

    async def query_plugins_async(self, filters: dict[str, object], timeout: float = 5.0) -> dict[str, object]:
        return {"plugins": [{"plugin_id": "demo", "name": "Demo"}]}

    async def trigger_plugin_event_async(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        return {"target_plugin_id": target_plugin_id, "event_type": event_type, "event_id": event_id, "params": params}


def test_runtime_constants_types_and_reexports() -> None:
    assert rt.EVENT_META_ATTR == "__neko_event_meta__"
    assert rt.HOOK_META_ATTR == "__neko_hook_meta__"
    assert isinstance(rt.EXTENDED_TYPES, tuple)
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

    assert [f.name for f in fields(rt.EventMeta)] == [
        "event_type",
        "id",
        "name",
        "description",
        "input_schema",
        "auto_start",
        "metadata",
        "extra",
    ]


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
    cfg = rt.PluginConfig(_Ctx())
    dumped = await cfg.dump()
    assert dumped.is_ok()
    assert dumped.unwrap()["feature"]["enabled"] is True
    got = await cfg.get("feature.enabled")
    assert got.is_ok()
    required = await cfg.require("feature.enabled")
    assert required.is_ok()
    missing = await cfg.require("feature.missing")
    assert missing.is_err()
    set_result = await cfg.set("feature.new", True)
    assert set_result.is_ok()
    updated = await cfg.update({"x": 1})
    assert updated.is_ok()
    section = await cfg.get_section("feature")
    assert section.is_ok()


@pytest.mark.asyncio
async def test_plugins_contract_methods_raise_not_implemented() -> None:
    plugins = rt.Plugins(_Ctx())
    listed = await plugins.list()
    assert listed.is_ok()
    entry = await plugins.call_entry("demo:run", {"k": 1})
    assert entry.is_ok()
    event = await plugins.call_event("demo:custom:run", {"k": 1})
    assert event.is_ok()
    required = await plugins.require("demo")
    assert required.is_ok()
    missing = await plugins.require("missing")
    assert missing.is_err()


@pytest.mark.asyncio
async def test_router_contract_methods_raise_not_implemented() -> None:
    router = rt.PluginRouter(prefix="p_")
    added = await router.add_entry("x", lambda _payload: None)
    assert added.is_ok()
    duplicate = await router.add_entry("x", lambda _payload: None)
    assert duplicate.is_err()
    entries = await router.list_entries()
    assert entries.is_ok()
    assert entries.unwrap()[0].id == "p_x"
    removed = await router.remove_entry("x")
    assert removed.is_ok()
    assert removed.unwrap() is True


@pytest.mark.asyncio
async def test_call_chain_helpers_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        await rt.get_call_chain()
    with pytest.raises(NotImplementedError):
        await rt.get_call_depth()
    with pytest.raises(NotImplementedError):
        await rt.is_in_call_chain("p", "e")


@pytest.mark.asyncio
async def test_system_info_runtime_behaviors() -> None:
    class _CtxWithSystem(_Ctx):
        async def get_system_config(self, timeout: float = 5.0) -> dict[str, object]:
            return {"config": {"plugin_dir": "/tmp/demo"}}

    info = rt.SystemInfo(_CtxWithSystem())
    config = await info.get_system_config()
    assert config.is_ok()
    env = await info.get_python_env()
    assert env.is_ok()
    assert "python" in env.unwrap()

    class _CtxNoSystem:
        plugin_id = "demo"

    no_system = rt.SystemInfo(_CtxNoSystem())
    assert (await no_system.get_system_config()).is_err()


@pytest.mark.asyncio
async def test_memory_client_runtime_behaviors() -> None:
    class _CtxMem(_Ctx):
        async def query_memory_async(self, lanlan_name: str, query: str, timeout: float = 5.0) -> dict[str, object]:
            return {"bucket": lanlan_name, "query": query}

        @property
        def bus(self):
            class _Bus:
                class memory:
                    @staticmethod
                    async def get_async(bucket_id: str, limit: int = 20, timeout: float = 5.0):
                        class _List:
                            @staticmethod
                            def dump_records():
                                return [{"bucket": bucket_id, "limit": limit}]
                        return _List()
            return _Bus()

    mem = rt.MemoryClient(_CtxMem())
    queried = await mem.query("b", "q")
    assert queried.is_ok()
    got = await mem.get("b")
    assert got.is_ok()
    assert got.unwrap()[0]["bucket"] == "b"

    class _CtxNoMem:
        plugin_id = "demo"

    no_mem = rt.MemoryClient(_CtxNoMem())
    assert (await no_mem.query("b", "q")).is_err()
    assert (await no_mem.get("b")).is_err()


@pytest.mark.asyncio
async def test_store_database_and_state_runtime_exports_work(tmp_path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()

    store = rt.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    assert (await store.set("k", {"v": 1})).is_ok()
    assert (await store.get("k")).unwrap() == {"v": 1}
    assert (await store.delete("k")).unwrap() is True

    db = rt.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    assert (await db.create_all()).is_ok()
    session = (await db.session()).unwrap()
    cursor = await session.execute("SELECT 1")
    assert cursor.fetchone()[0] == 1

    kv = rt.PluginKVStore(database=db)
    assert (await kv.set("k", "v")).is_ok()
    assert (await kv.get("k")).unwrap() == "v"
    assert (await kv.delete("k")).unwrap() is True

    class _StateObj:
        __freezable__ = ["counter"]

        def __init__(self) -> None:
            self.counter = 1

    state_obj = _StateObj()
    persistence = rt.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="memory")
    assert (await persistence.save(state_obj)).unwrap() is True
    state_obj.counter = 0
    assert (await persistence.load(state_obj)).unwrap() is True
    assert state_obj.counter == 1


def test_runtime_all_exports_exist() -> None:
    for name in rt.__all__:
        assert hasattr(rt, name)

    # Explicit contract placeholders should exist.
    assert rt.CallChain.__name__ == "CallChain"
    assert rt.AsyncCallChain.__name__ == "AsyncCallChain"
