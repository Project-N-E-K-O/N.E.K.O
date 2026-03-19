from __future__ import annotations

from dataclasses import dataclass

from plugin.sdk_v2.shared.core import entry_runtime
from plugin.sdk_v2.shared.core.entry_runtime import prepare_entry_kwargs, resolve_entry_timeout
from plugin.sdk_v2.shared.core.events import EventMeta


class _Model:
    def __init__(self, *, name: str, count: int = 0) -> None:
        self.name = name
        self.count = int(count)

    @classmethod
    def model_validate(cls, payload: dict[str, object]) -> "_Model":
        return cls(
            name=str(payload["name"]),
            count=int(payload.get("count", 0) or 0),
        )

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        assert mode == "python"
        return {"name": self.name, "count": self.count}


def test_resolve_entry_timeout_prefers_explicit_timeout() -> None:
    meta = EventMeta(
        event_type="plugin_entry",
        id="run",
        timeout=3.5,
        metadata={"timeout": 9},
    )
    assert resolve_entry_timeout(meta, 10.0) == 3.5
    assert resolve_entry_timeout(EventMeta(event_type="plugin_entry", id="run", timeout=0), 10.0) is None
    assert resolve_entry_timeout(None, 10.0) == 10.0


def test_prepare_entry_kwargs_injects_params_model() -> None:
    async def handler(params: _Model) -> str:
        return params.name

    meta = EventMeta(
        event_type="plugin_entry",
        id="run",
        params=_Model,
        model_validate=True,
    )

    kwargs = prepare_entry_kwargs(
        plugin_id="demo",
        entry_id="run",
        handler=handler,
        meta=meta,
        args={"name": "alice", "count": "2", "_ctx": {"run_id": "r1"}},
    )

    assert set(kwargs.keys()) == {"params"}
    assert isinstance(kwargs["params"], _Model)
    assert kwargs["params"].name == "alice"
    assert kwargs["params"].count == 2


def test_prepare_entry_kwargs_expands_validated_payload_when_no_params_slot() -> None:
    async def handler(name: str, count: int, **kwargs: object) -> str:
        return name

    meta = EventMeta(
        event_type="plugin_entry",
        id="run",
        params=_Model,
        model_validate=True,
    )

    kwargs = prepare_entry_kwargs(
        plugin_id="demo",
        entry_id="run",
        handler=handler,
        meta=meta,
        args={"name": "alice", "count": "2", "_ctx": {"run_id": "r1"}},
    )

    assert kwargs["name"] == "alice"
    assert kwargs["count"] == 2
    assert kwargs["_ctx"] == {"run_id": "r1"}


def test_prepare_entry_kwargs_raises_plugin_execution_error_on_invalid_payload() -> None:
    async def handler(name: str) -> str:
        return name

    meta = EventMeta(
        event_type="plugin_entry",
        id="run",
        params=_Model,
        model_validate=True,
    )

    try:
        prepare_entry_kwargs(
            plugin_id="demo",
            entry_id="run",
            handler=handler,
            meta=meta,
            args={"count": "2"},
        )
    except Exception as error:
        assert "parameter validation failed" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected validation failure")


def test_resolve_entry_timeout_uses_extra_metadata_and_ignores_invalid_values() -> None:
    extra_meta = type("Meta", (), {"timeout": None, "extra": {"timeout": "4.5"}, "metadata": {"timeout": 9}})()
    metadata_meta = type("Meta", (), {"timeout": None, "extra": {"timeout": "bad"}, "metadata": {"timeout": "6"}})()
    invalid_meta = type("Meta", (), {"timeout": None, "extra": {"timeout": object()}, "metadata": {"timeout": None}})()

    assert resolve_entry_timeout(extra_meta, 10.0) == 4.5
    assert resolve_entry_timeout(metadata_meta, 10.0) == 6.0
    assert resolve_entry_timeout(invalid_meta, 7.0) == 7.0


def test_prepare_entry_kwargs_short_circuits_without_validation() -> None:
    args = {"name": "alice", "_ctx": {"run_id": "r1"}}

    assert prepare_entry_kwargs(
        plugin_id="demo",
        entry_id="run",
        handler=lambda **kwargs: kwargs,
        meta=None,
        args=args,
    ) == args

    assert prepare_entry_kwargs(
        plugin_id="demo",
        entry_id="run",
        handler=lambda **kwargs: kwargs,
        meta=EventMeta(event_type="plugin_entry", id="run", params=None, model_validate=True),
        args=args,
    ) == args

    assert prepare_entry_kwargs(
        plugin_id="demo",
        entry_id="run",
        handler=lambda **kwargs: kwargs,
        meta=EventMeta(event_type="plugin_entry", id="run", params=_Model, model_validate=False),
        args=args,
    ) == args


def test_prepare_entry_kwargs_supports_parse_obj_and_constructor_models() -> None:
    class _ParseModel:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        @classmethod
        def parse_obj(cls, payload: dict[str, object]) -> "_ParseModel":
            return cls(payload)

        def dict(self) -> dict[str, object]:
            return dict(self.payload)

    def parse_handler(payload: _ParseModel) -> None:
        return None

    parsed = prepare_entry_kwargs(
        plugin_id="demo",
        entry_id="parse",
        handler=parse_handler,
        meta=EventMeta(event_type="plugin_entry", id="parse", params=_ParseModel, model_validate=True),
        args={"name": "alice"},
    )
    assert isinstance(parsed["payload"], _ParseModel)
    assert parsed["payload"].payload == {"name": "alice"}

    class _CtorModel:
        def __init__(self, **payload: object) -> None:
            self.name = str(payload["name"])

    def ctor_handler(*items: object, model) -> None:
        return None

    constructed = prepare_entry_kwargs(
        plugin_id="demo",
        entry_id="ctor",
        handler=ctor_handler,
        meta=EventMeta(event_type="plugin_entry", id="ctor", params=_CtorModel, model_validate=True),
        args={"name": "bob"},
    )
    assert isinstance(constructed["model"], _CtorModel)
    assert constructed["model"].name == "bob"


def test_entry_runtime_internal_dump_and_injection_helpers() -> None:
    class _DictModel:
        def dict(self) -> dict[str, object]:
            return {"name": "alice"}

    @dataclass
    class _DataClassModel:
        name: str

    class _AttrModel:
        def __init__(self) -> None:
            self.name = "attr"

    class _Empty:
        __slots__ = ()

    assert entry_runtime._dump_model_instance(_DictModel()) == {"name": "alice"}
    assert entry_runtime._dump_model_instance({"name": "mapping"}) == {"name": "mapping"}
    assert entry_runtime._dump_model_instance(_DataClassModel(name="dc")) == {"name": "dc"}
    assert entry_runtime._dump_model_instance(_AttrModel()) == {"name": "attr"}
    assert entry_runtime._dump_model_instance(_Empty()) == {}

    annotated_signature = entry_runtime.inspect.Signature(
        parameters=[
            entry_runtime.inspect.Parameter(
                "payload",
                kind=entry_runtime.inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=_AttrModel,
            )
        ]
    )
    assert entry_runtime._resolve_model_injection_name(annotated_signature, _AttrModel, {"name": "alice"}) == "payload"

    signature = entry_runtime.inspect.signature(lambda *items, model: None)
    assert entry_runtime._resolve_model_injection_name(signature, object, {"name": "alice"}) == "model"
