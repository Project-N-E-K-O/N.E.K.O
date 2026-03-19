from __future__ import annotations

from dataclasses import fields

import pytest

from plugin.sdk_v2.plugin import runtime as rt
from plugin.sdk_v2.shared.models.errors import ErrorCode
from plugin.sdk_v2.shared.constants import SDK_VERSION


class _Ctx:
    plugin_id = "demo"

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"config": {"feature": {"enabled": True}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> dict[str, object]:
        return {"config": updates}

    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> dict[str, object]:
        return {"plugins": [{"plugin_id": "demo", "name": "Demo"}]}

    async def trigger_plugin_event(
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


def test_runtime_typed_structures_and_dataclasses() -> None:
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
        "kind",
        "auto_start",
        "persist",
        "params",
        "model_validate",
        "timeout",
        "llm_result_fields",
        "llm_result_schema",
        "llm_result_model",
        "extra",
        "metadata",
    ]


def test_runtime_error_classes_construct() -> None:
    assert isinstance(rt.PluginConfigError("e"), RuntimeError)
    assert isinstance(rt.PluginCallError("e"), RuntimeError)
    assert isinstance(rt.PluginRouterError("e"), RuntimeError)
    assert isinstance(rt.CircularCallError("e"), RuntimeError)
    assert isinstance(rt.CallChainTooDeepError("e"), RuntimeError)

    error = rt.PluginCallError(
        "boom",
        op_name="plugins.call_event_json",
        event_ref="demo:event:run",
        timeout=7.0,
    )
    assert error.context["op_name"] == "plugins.call_event_json"
    assert error.context["event_ref"] == "demo:event:run"
    assert error.context["timeout"] == 7.0


def test_hook_executor_mixin_not_implemented() -> None:
    mixin = object.__new__(rt.HookExecutorMixin)
    with pytest.raises(NotImplementedError):
        mixin.__init_hook_executor__()


@pytest.mark.asyncio
async def test_plugin_config_contract_methods_raise_not_implemented() -> None:
    cfg = rt.PluginConfig(_Ctx())
    dumped = await cfg.dump()
    assert dumped["feature"]["enabled"] is True
    got = await cfg.get("feature.enabled")
    assert got is True
    required = await cfg.require("feature.enabled")
    assert required is True
    with pytest.raises(Exception):
        await cfg.require("feature.missing")
    with pytest.raises(Exception):
        await cfg.set("feature.new", True)
    with pytest.raises(Exception):
        await cfg.update({"x": 1})


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
async def test_plugin_runtime_plugins_list_get_exists_and_ids_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = rt.Plugins(_Ctx())

    async def _list_ok(self, *, timeout: float = 5.0):
        return rt.Ok(
            [
                {"plugin_id": "enabled", "enabled": True},
                {"plugin_id": "disabled", "enabled": False},
                {"plugin_id": 123, "enabled": True},
            ]
        )

    monkeypatch.setattr(rt._plugins.Plugins, "list", _list_ok)

    assert [item["plugin_id"] for item in (await plugins.list(enabled=True)).unwrap()] == ["enabled", 123]
    assert [item["plugin_id"] for item in (await plugins.list(enabled=False)).unwrap()] == ["disabled"]
    assert (await plugins.list_ids(enabled=True)).unwrap() == ["enabled"]
    assert (await plugins.get("enabled")).unwrap()["plugin_id"] == "enabled"
    assert (await plugins.get("missing")).unwrap() is None
    assert (await plugins.exists("enabled")).unwrap() is True
    assert (await plugins.exists("missing")).unwrap() is False

    async def _list_err(*args, **kwargs):
        return rt.Err(rt.PluginCallError("boom"))

    monkeypatch.setattr(plugins, "list", _list_err)
    assert (await plugins.list_ids()).is_err()


@pytest.mark.asyncio
async def test_plugin_runtime_plugins_require_enabled_and_json_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = rt.Plugins(_Ctx())

    async def _require_err(self, plugin_id: str, *, timeout: float = 5.0):
        return rt.Err(rt.PluginCallError("missing", plugin_id=plugin_id, timeout=timeout))

    monkeypatch.setattr(rt._plugins.Plugins, "require", _require_err)
    assert (await plugins.require_enabled("demo")).is_err()

    async def _require_disabled(self, plugin_id: str, *, timeout: float = 5.0):
        return rt.Ok({"plugin_id": plugin_id, "enabled": False})

    monkeypatch.setattr(rt._plugins.Plugins, "require", _require_disabled)
    disabled = await plugins.require_enabled("demo")
    assert disabled.is_err()
    assert isinstance(disabled.error, rt.PluginCallError)
    assert disabled.error.context["op_name"] == "plugins.require_enabled"

    async def _require_enabled(self, plugin_id: str, *, timeout: float = 5.0):
        return rt.Ok({"plugin_id": plugin_id, "enabled": True})

    monkeypatch.setattr(rt._plugins.Plugins, "require", _require_enabled)
    assert (await plugins.require_enabled("demo")).unwrap()["plugin_id"] == "demo"

    async def _call_entry_err(*args, **kwargs):
        return rt.Err(rt.PluginCallError("entry boom"))

    async def _call_entry_non_object(*args, **kwargs):
        return rt.Ok("bad")

    async def _call_entry_object(*args, **kwargs):
        return rt.Ok({"ok": True})

    monkeypatch.setattr(plugins, "call_entry", _call_entry_err)
    assert (await plugins.call_entry_json("demo:run")).is_err()
    monkeypatch.setattr(plugins, "call_entry", _call_entry_non_object)
    non_object_entry = await plugins.call_entry_json("demo:run")
    assert non_object_entry.is_err()
    assert isinstance(non_object_entry.error, rt.PluginCallError)
    monkeypatch.setattr(plugins, "call_entry", _call_entry_object)
    assert (await plugins.call_entry_json("demo:run")).unwrap() == {"ok": True}
    monkeypatch.setattr(plugins, "call_entry", lambda *args, **kwargs: _call_entry_object(*args, **kwargs))
    assert (await plugins.call_entry_json("demo:run")).unwrap() == {"ok": True}

    async def _call_event_err(*args, **kwargs):
        return rt.Err(rt.PluginCallError("event boom"))

    async def _call_event_non_object(*args, **kwargs):
        return rt.Ok("bad")

    async def _call_event_none(*args, **kwargs):
        return rt.Ok(None)

    monkeypatch.setattr(plugins, "call_event", _call_event_err)
    assert (await plugins.call_event_json("demo:event:run")).is_err()
    monkeypatch.setattr(plugins, "call_event", _call_event_non_object)
    non_object_event = await plugins.call_event_json("demo:event:run")
    assert non_object_event.is_err()
    assert isinstance(non_object_event.error, rt.PluginCallError)
    monkeypatch.setattr(plugins, "call_event", _call_event_none)
    assert (await plugins.call_event_json("demo:event:run")).unwrap() is None


@pytest.mark.asyncio
async def test_plugin_runtime_plugins_error_passthrough_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = rt.Plugins(_Ctx())

    async def _list_err(self, *, timeout: float = 5.0):
        return rt.Err(rt.PluginCallError("boom", timeout=timeout))

    monkeypatch.setattr(rt._plugins.Plugins, "list", _list_err)

    assert (await plugins.list(enabled=True)).is_err()
    assert (await plugins.get("demo")).is_err()
    assert (await plugins.exists("demo")).is_err()


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
async def test_call_chain_helpers_runtime() -> None:
    rt.CallChain.clear()
    assert (await rt.get_call_chain()).unwrap() == []
    assert (await rt.get_call_depth()).unwrap() == 0
    with rt.CallChain.track("p.entry:run"):
        assert (await rt.get_call_depth()).unwrap() == 1
        assert (await rt.is_in_call_chain("p", "run")).unwrap() is True
        assert (await rt.get_call_chain()).unwrap()[0].plugin_id == "p"
    assert (await rt.is_in_call_chain("p", "run")).unwrap() is False


@pytest.mark.asyncio
async def test_system_info_runtime_behaviors() -> None:
    class _CtxWithSystem(_Ctx):
        async def get_system_config(self, timeout: float = 5.0) -> dict[str, object]:
            return {"config": {"plugin_dir": "/tmp/demo"}}

    info = rt.SystemInfo(_CtxWithSystem())
    config = await info.get_system_config()
    assert config.is_ok()
    settings = await info.get_server_settings()
    assert settings.is_ok()
    assert settings.unwrap() == {"plugin_dir": "/tmp/demo"}
    env = await info.get_python_env()
    assert env.is_ok()
    assert "python" in env.unwrap()

    class _CtxNoSystem:
        plugin_id = "demo"

    no_system = rt.SystemInfo(_CtxNoSystem())
    assert (await no_system.get_system_config()).is_err()
    assert (await no_system.get_server_settings()).is_err()


@pytest.mark.asyncio
async def test_memory_client_runtime_behaviors() -> None:
    class _CtxMem(_Ctx):
        async def query_memory(self, lanlan_name: str, query: str, timeout: float = 5.0) -> dict[str, object]:
            return {"bucket": lanlan_name, "query": query}

        @property
        def bus(self):
            class _Bus:
                class memory:
                    @staticmethod
                    async def get(bucket_id: str, limit: int = 20, timeout: float = 5.0):
                        class _List:
                            @staticmethod
                            def dump_records():
                                return [{"bucket": bucket_id, "limit": limit}]
                        return rt.Ok(_List())
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
