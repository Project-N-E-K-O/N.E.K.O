from __future__ import annotations

import inspect
import asyncio
from contextlib import suppress
from typing import Any, Callable, Sequence, Union, cast

__all__ = [
    "_dedupe_key_from_record",
    "_sort_bus_value",
    "_get_sort_field_from_record",
    "_get_field_from_record",
    "_cast_bus_value",
    "_cancel_timer_best_effort",
    "_build_watcher_injected_callback",
    "_infer_bus_from_plan",
    "_compute_watcher_delta",
    "_dispatch_watcher_callbacks",
    "_snapshot_watcher_callbacks",
    "_normalize_watch_rules",
    "_register_watcher_callback",
    "_build_bus_subscribe_request",
    "_extract_sub_id",
    "_build_bus_unsubscribe_request",
    "_schedule_watcher_tick_debounced",
    "_freeze_plan_value",
    "_replay_cache_key_get",
    "_replay_cache_key_unary",
    "_message_plane_replay_rpc",
    "_rebuild_records_from_plane_items",
    "_apply_reload_inplace_basic",
    "BusListCore",
    "BusListWatcherCore",
]


def _dedupe_key_from_record(item: Any) -> tuple[str, Any]:
    for attr in ("message_id", "event_id", "lifecycle_id", "trace_id"):
        try:
            v = getattr(item, attr, None)
        except Exception:
            v = None
        if isinstance(v, str) and v:
            return (attr, v)

    raw = None
    try:
        raw = getattr(item, "raw", None)
    except Exception:
        raw = None
    if isinstance(raw, dict):
        for k in ("message_id", "event_id", "lifecycle_id", "trace_id"):
            v = raw.get(k)
            if isinstance(v, str) and v:
                return (k, v)

    try:
        dumped = item.dump()
        fp = tuple(sorted((str(k), repr(v)) for k, v in dumped.items()))
        return ("dump", fp)
    except Exception:
        return ("object", id(item))


def _sort_bus_value(v: Any) -> tuple[int, Any]:
    if v is None:
        return (2, "")
    if isinstance(v, (int, float)):
        return (0, v)
    return (1, str(v))


def _get_sort_field_from_record(item: Any, field: str) -> Any:
    try:
        return getattr(item, field)
    except Exception:
        pass

    raw = None
    try:
        raw = getattr(item, "raw", None)
    except Exception:
        raw = None
    if isinstance(raw, dict) and field in raw:
        return raw.get(field)

    try:
        dumped = item.dump()
        return dumped.get(field)
    except Exception:
        return None


def _rebuild_records_from_plane_items(bus: str, items: list[dict[str, Any]]) -> list[Any]:
    records: list[Any] = []
    try:
        if bus == "messages":
            from plugin.core.bus.messages import MessageRecord

            for plane_item in items:
                index_data = plane_item.get("index")
                payload = plane_item.get("payload")
                if isinstance(index_data, dict):
                    records.append(MessageRecord.from_index(index_data, payload if isinstance(payload, dict) else None))
                elif isinstance(payload, dict):
                    records.append(MessageRecord.from_raw(payload))
        elif bus == "events":
            from plugin.core.bus.events import EventRecord

            for plane_item in items:
                index_data = plane_item.get("index")
                payload = plane_item.get("payload")
                if isinstance(index_data, dict):
                    records.append(EventRecord.from_index(index_data, payload if isinstance(payload, dict) else None))
                elif isinstance(payload, dict):
                    records.append(EventRecord.from_raw(payload))
        elif bus == "lifecycle":
            from plugin.core.bus.lifecycle import LifecycleRecord

            for plane_item in items:
                index_data = plane_item.get("index")
                payload = plane_item.get("payload")
                if isinstance(index_data, dict):
                    records.append(LifecycleRecord.from_index(index_data, payload if isinstance(payload, dict) else None))
                elif isinstance(payload, dict):
                    records.append(LifecycleRecord.from_raw(payload))
    except Exception:
        return []
    return records


def _apply_reload_inplace_basic(target: Any, refreshed: Any, ctx: Any) -> None:
    target._items = list(refreshed.dump_records())
    target._ctx = ctx
    target._cache_valid = True
    if hasattr(target, "plugin_id") and hasattr(refreshed, "plugin_id"):
        with suppress(Exception):
            setattr(target, "plugin_id", getattr(refreshed, "plugin_id"))


def _compute_watcher_delta(
    *,
    op: str,
    refreshed_items: list[Any],
    last_keys: set[tuple[str, Any]],
    dedupe_key: Callable[[Any], tuple[str, Any]],
) -> tuple[list[Any], tuple[tuple[str, Any], ...], set[tuple[str, Any]], list[str], str]:
    new_keys: set[tuple[str, Any]] = {dedupe_key(x) for x in refreshed_items}

    added_items: list[Any] = []
    for x in refreshed_items:
        k = dedupe_key(x)
        if k not in last_keys:
            added_items.append(x)

    removed_keys: tuple[tuple[str, Any], ...] = tuple(k for k in last_keys if k not in new_keys)

    fired: list[str] = []
    if added_items:
        fired.append("add")
    if removed_keys:
        fired.append("del")
    if added_items or removed_keys:
        fired.append("change")

    kind = op if op in ("add", "del", "change") else "change"
    return added_items, removed_keys, new_keys, fired, kind


def _dispatch_watcher_callbacks(
    callbacks: list[tuple[Callable[[Any], None], tuple[str, ...]]],
    fired: list[str],
    delta: Any,
) -> None:
    for fn, rules in callbacks:
        if any(r in fired for r in rules):
            try:
                fn(delta)
            except Exception:
                continue


def _snapshot_watcher_callbacks(
    callbacks: list[tuple[Callable[[Any], None], tuple[str, ...]]],
    lock: Any,
) -> list[tuple[Callable[[Any], None], tuple[str, ...]]]:
    if lock is not None:
        with lock:
            return list(callbacks)
    return list(callbacks)


def _normalize_watch_rules(on: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(on, str):
        return (on,)
    return tuple(on)


def _register_watcher_callback(
    callbacks: list[tuple[Callable[[Any], None], tuple[str, ...]]],
    lock: Any,
    wrapped: Callable[[Any], None],
    rules: tuple[str, ...],
) -> None:
    if lock is not None:
        with lock:
            callbacks.append((wrapped, rules))
        return
    callbacks.append((wrapped, rules))


class BusListCore:
    """Low-coupling BusList methods migrated from types.py."""

    def reload_with(self, ctx: Any = None, *, inplace: bool = False) -> Any:
        raise NotImplementedError()

    def _dedupe_key(self, item: Any) -> tuple[str, Any]:
        return _dedupe_key_from_record(item)

    def _sort_value(self, v: Any) -> tuple[int, Any]:
        return _sort_bus_value(v)

    def _get_sort_field(self, item: Any, field: str) -> Any:
        return _get_sort_field_from_record(item, field)

    def _get_field(self, item: Any, field: str) -> Any:
        return _get_field_from_record(item, field)

    def _cast_value(self, v: Any, cast: str | None) -> Any:
        return _cast_bus_value(v, cast)

    def reload(self, ctx: Any = None) -> Any:
        return self.reload_with(ctx)

    async def reload_with_async(
        self,
        ctx: Any = None,
        *,
        inplace: bool = False,
    ) -> Any:
        if ctx is None:
            return await asyncio.to_thread(
                self.reload_with,
                inplace=inplace,
            )
        return await asyncio.to_thread(
            self.reload_with,
            ctx,
            inplace=inplace,
        )


class BusListWatcherCore:
    """Partial BusListWatcher behavior migrated from types.py.

    This mixin assumes subclasses provide `_callbacks` and `_lock` fields.
    """

    _callbacks: list[tuple[Callable[[Any], None], tuple[str, ...]]]
    _lock: Any
    _unsub: Callable[[], None] | None
    _sub_id: str | None
    _ctx: Any
    _bus: str
    _list: Any

    def _watcher_set(self, sub_id: str) -> None:
        raise NotImplementedError()

    def _watcher_pop(self, sub_id: str) -> None:
        raise NotImplementedError()

    def _schedule_tick(self, op: str, payload: dict[str, Any] | None = None) -> None:
        raise NotImplementedError()

    def _state_subscribe(self, bus: str, on_event: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        from plugin.core.state import state

        return state.bus_change_hub.subscribe(bus, on_event)

    def _make_injected_callback(self, fn: Callable[..., None]) -> Callable[[Any], None]:
        return _build_watcher_injected_callback(fn)

    def subscribe(
        self,
        *,
        on: Union[str, Sequence[str]] = ("add",),
    ) -> Callable[[Callable[..., None]], Callable[..., None]]:
        rules = _normalize_watch_rules(cast(Union[str, list[str], tuple[str, ...]], on))

        def _decorator(fn: Callable[..., None]) -> Callable[..., None]:
            wrapped = self._make_injected_callback(fn)
            _register_watcher_callback(self._callbacks, self._lock, wrapped, rules)
            return fn

        return _decorator

    def start(self) -> Any:
        if self._unsub is not None or self._sub_id is not None:
            return self

        if getattr(self._ctx, "_plugin_comm_queue", None) is not None and hasattr(self._ctx, "_send_request_and_wait"):
            res = self._ctx._send_request_and_wait(
                method_name="bus_subscribe",
                request_type="BUS_SUBSCRIBE",
                request_data=_build_bus_subscribe_request(self._bus, self._list.trace_tree_dump()),
                timeout=5.0,
                wrap_result=True,
            )
            sub_id = _extract_sub_id(res)
            if not sub_id:
                raise RuntimeError("BUS_SUBSCRIBE failed: missing sub_id")
            self._sub_id = sub_id
            self._watcher_set(sub_id)
            return self

        def _on_event(_op: str, _payload: dict[str, Any]) -> None:
            try:
                self._schedule_tick(_op, _payload)
            except Exception:
                return

        self._unsub = self._state_subscribe(self._bus, _on_event)
        return self

    def stop(self) -> None:
        if self._sub_id is not None:
            sid = self._sub_id
            self._sub_id = None
            self._watcher_pop(sid)

            with suppress(Exception):
                if getattr(self._ctx, "_plugin_comm_queue", None) is not None and hasattr(self._ctx, "_send_request_and_wait"):
                    self._ctx._send_request_and_wait(
                        method_name="bus_unsubscribe",
                        request_type="BUS_UNSUBSCRIBE",
                        request_data=_build_bus_unsubscribe_request(self._bus, sid),
                        timeout=3.0,
                        wrap_result=True,
                    )
            return

        if self._unsub is None:
            return
        try:
            self._unsub()
        finally:
            self._unsub = None


def _build_bus_subscribe_request(bus: str, plan_dump: Any) -> dict[str, Any]:
    return {
        "bus": bus,
        "rules": ["add", "del", "change"],
        "deliver": "delta",
        "plan": plan_dump,
    }


def _extract_sub_id(res: Any) -> str | None:
    if isinstance(res, dict):
        sub_id = res.get("sub_id")
        if isinstance(sub_id, str) and sub_id:
            return sub_id
    return None


def _build_bus_unsubscribe_request(bus: str, sub_id: str) -> dict[str, Any]:
    return {"bus": bus, "sub_id": sub_id}


def _freeze_plan_value(x: Any) -> Any:
    try:
        if isinstance(x, dict):
            return tuple(sorted((str(k), _freeze_plan_value(v)) for k, v in x.items()))
        if isinstance(x, (list, tuple)):
            return tuple(_freeze_plan_value(v) for v in x)
        if isinstance(x, set):
            return tuple(sorted(_freeze_plan_value(v) for v in x))
        if isinstance(x, (str, int, float, bool, type(None))):
            return x
        return repr(x)
    except Exception:
        return repr(x)


def _replay_cache_key_get(bus: str, params: dict[str, Any]) -> tuple[str, str, Any]:
    return ("get", str(bus), _freeze_plan_value(dict(params or {})))


def _replay_cache_key_unary(op: str, params: dict[str, Any], child_key: Any) -> tuple[str, str, Any, Any]:
    return ("unary", str(op), _freeze_plan_value(dict(params or {})), child_key)


def _message_plane_replay_rpc(
    *,
    ctx: Any,
    bus: str,
    plan: Any,
    timeout: float,
    serialize_plan: Callable[[Any], Any],
) -> list[dict[str, Any]] | None:
    try:
        import time as _time
        import json as _json
        import os as _os
        import uuid as _uuid
        import ormsgpack as _ormsgpack
        try:
            import zmq as _zmq
        except Exception:
            _zmq = None
        if _zmq is None:
            return None
        from plugin.settings import MESSAGE_PLANE_ZMQ_RPC_ENDPOINT

        plan_dict = serialize_plan(plan)
        if plan_dict is None:
            return None
        endpoint = str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT)
        if not endpoint:
            return None

        sock = None
        try:
            import threading
            tls = getattr(ctx, "_mp_replay_tls", None)
            if tls is None:
                tls = threading.local()
                setattr(ctx, "_mp_replay_tls", tls)
            sock = getattr(tls, "sock", None)
        except Exception:
            try:
                sock = getattr(ctx, "_mp_replay_sock", None)
            except Exception:
                sock = None

        if sock is None:
            zctx = _zmq.Context.instance()
            sock = zctx.socket(_zmq.DEALER)
            with suppress(Exception):
                ident = f"mp-replay:{getattr(ctx, 'plugin_id', '')}:{int(_time.time() * 1000)}".encode("utf-8")
                sock.setsockopt(_zmq.IDENTITY, ident)
            with suppress(Exception):
                sock.setsockopt(_zmq.LINGER, 0)
            sock.connect(endpoint)

            try:
                import threading
                tls = getattr(ctx, "_mp_replay_tls", None)
                if tls is not None:
                    tls.sock = sock
                else:
                    setattr(ctx, "_mp_replay_sock", sock)
            except Exception:
                with suppress(Exception):
                    setattr(ctx, "_mp_replay_sock", sock)

        req_id = f"replay:{getattr(ctx, 'plugin_id', '')}:{_uuid.uuid4()}"
        try:
            light_mode = str(_os.getenv("NEKO_BUSLIST_RELOAD_FULL_LIGHT", "0")).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        except Exception:
            light_mode = False
        req = {
            "v": 1,
            "op": "bus.replay",
            "req_id": req_id,
            "from_plugin": getattr(ctx, "plugin_id", ""),
            "args": {"store": str(bus), "plan": plan_dict, "light": bool(light_mode)},
        }
        try:
            raw = _ormsgpack.packb(req)
        except Exception:
            raw = _json.dumps(req, ensure_ascii=False).encode("utf-8")
        try:
            sock.send(raw, flags=0)
        except Exception:
            return None

        deadline = _time.time() + max(0.0, float(timeout))
        while True:
            remaining = deadline - _time.time()
            if remaining <= 0:
                return None
            try:
                if sock.poll(timeout=int(remaining * 1000), flags=_zmq.POLLIN) == 0:
                    continue
            except Exception:
                return None
            try:
                resp_raw = sock.recv(flags=0)
            except Exception:
                return None
            resp = None
            try:
                resp = _ormsgpack.unpackb(resp_raw)
            except Exception:
                try:
                    resp = _json.loads(resp_raw.decode("utf-8"))
                except Exception:
                    resp = None
            if not isinstance(resp, dict):
                continue
            if resp.get("req_id") != req_id:
                continue
            if not resp.get("ok"):
                return None
            result = resp.get("result")
            if not isinstance(result, dict):
                return None
            items = result.get("items")
            if not isinstance(items, list):
                return None
            out: list[dict[str, Any]] = []
            for item_entry in items:
                if isinstance(item_entry, dict):
                    out.append(item_entry)
            return out
    except Exception:
        return None


def _get_field_from_record(item: Any, field: str) -> Any:
    try:
        return getattr(item, field)
    except Exception:
        pass

    raw = None
    try:
        raw = getattr(item, "raw", None)
    except Exception:
        raw = None
    if isinstance(raw, dict):
        return raw.get(field)

    try:
        dumped = item.dump()
        if isinstance(dumped, dict):
            return dumped.get(field)
    except Exception:
        return None
    return None


def _cast_bus_value(v: Any, cast: str | None) -> Any:
    if cast is None:
        return v
    c = str(cast).strip().lower()
    if c in ("int", "i"):
        try:
            return int(str(v).strip())
        except Exception:
            return 0
    if c in ("float", "f"):
        try:
            return float(str(v).strip())
        except Exception:
            return 0.0
    if c in ("str", "s"):
        try:
            return "" if v is None else str(v)
        except Exception:
            return ""
    return v


def _cancel_timer_best_effort(timer: Any) -> None:
    try:
        if timer is not None:
            timer.cancel()
    except Exception:
        return


def _schedule_watcher_tick_debounced(
    watcher: Any,
    op: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if float(getattr(watcher, "_debounce_ms", 0.0) or 0.0) <= 0:
        watcher._tick(op, payload)
        return

    try:
        import threading

        delay = max(0.0, float(getattr(watcher, "_debounce_ms", 0.0) or 0.0) / 1000.0)
        normalized_payload = dict(payload or {}) if isinstance(payload, dict) else None

        lock = getattr(watcher, "_lock", None)
        if lock is not None:
            with lock:
                watcher._pending_op = str(op)
                watcher._pending_payload = normalized_payload
                prev_timer = getattr(watcher, "_debounce_timer", None)
                watcher._debounce_timer = None
        else:
            watcher._pending_op = str(op)
            watcher._pending_payload = normalized_payload
            prev_timer = getattr(watcher, "_debounce_timer", None)
            watcher._debounce_timer = None

        _cancel_timer_best_effort(prev_timer)

        def _fire() -> None:
            lock2 = getattr(watcher, "_lock", None)
            if lock2 is not None:
                with lock2:
                    pending = getattr(watcher, "_pending_op", None)
                    pending_payload = getattr(watcher, "_pending_payload", None)
                    watcher._pending_op = None
                    watcher._pending_payload = None
                    watcher._debounce_timer = None
            else:
                pending = getattr(watcher, "_pending_op", None)
                pending_payload = getattr(watcher, "_pending_payload", None)
                watcher._pending_op = None
                watcher._pending_payload = None
                watcher._debounce_timer = None

            with suppress(Exception):
                watcher._tick(str(pending or "change"), pending_payload)

        timer = threading.Timer(delay, _fire)
        timer.daemon = True
        if lock is not None:
            with lock:
                watcher._debounce_timer = timer
        else:
            watcher._debounce_timer = timer
        timer.start()
    except Exception:
        watcher._tick(op, payload)


def _build_watcher_injected_callback(fn: Callable[..., None]) -> Callable[[Any], None]:
    try:
        sig = inspect.signature(fn)
    except Exception:
        return cast(Callable[[Any], None], fn)

    params = list(sig.parameters.values())
    if len(params) == 1 and params[0].kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        return cast(Callable[[Any], None], fn)

    def _dump_record(rec: Any) -> Any:
        if hasattr(rec, "dump") and callable(getattr(rec, "dump")):
            try:
                return rec.dump()
            except Exception:
                return rec
        return rec

    def _wrapped(delta: Any) -> None:
        try:
            added = getattr(delta, "added", ())
            removed = getattr(delta, "removed", ())
            current = getattr(delta, "current", None)
            kind = getattr(delta, "kind", "change")
            mapping: dict[str, Any] = {
                "delta": delta,
                "d": delta,
                "list": current,
                "current": current,
                "buslist": current,
                "added": added,
                "removed": removed,
                "length": len(added),
                "len": len(added),
                "count": len(added),
                "kind": kind,
                "op": kind,
                "quickdump": tuple(_dump_record(x) for x in added),
            }

            kwargs: dict[str, Any] = {}
            for p in params:
                if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                    continue
                if p.name in mapping:
                    kwargs[p.name] = mapping[p.name]
                elif p.default is inspect._empty:
                    fn(delta)
                    return

            fn(**kwargs)
        except Exception:
            fn(delta)

    return _wrapped


def _infer_bus_from_plan(plan: Any, *, conflict_error: type[Exception]) -> str:
    if plan is None:
        return ""
    if hasattr(plan, "params") and isinstance(getattr(plan, "params", None), dict) and not hasattr(plan, "child"):
        return str(getattr(plan, "params", {}).get("bus") or "").strip()
    if hasattr(plan, "child"):
        return _infer_bus_from_plan(getattr(plan, "child", None), conflict_error=conflict_error)
    return ""


def __getattr__(name: str) -> Any:
    if name in {"BusList", "BusListDelta", "BusListWatcher"}:
        from plugin.core.bus import types as _types

        return getattr(_types, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
