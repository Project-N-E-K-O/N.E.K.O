# sdk_v2.plugin SPEC (SDD Contract)

## 1. Scope

`sdk_v2.plugin` is the standard plugin-facing API surface.
This spec defines:
- API names and signatures
- input/output data structures
- boundary and validation rules
- error semantics (`Result` and `ok/fail` envelopes)

Current phase is contract-only: runtime behavior is intentionally unimplemented.

## 2. Error Model

Two compatible layers are defined:

1. `Result` layer (primary internal contract)
- success: `Ok[T]`
- error: `Err[E]`
- helpers: `must`, `unwrap`, `bind_result`, `map_err_result`, etc.

2. Envelope layer (boundary compatibility)
- success: `ok(...)` => `{success: true, code, data, ...}`
- failure: `fail(...)` => `{success: false, code, error: {...}, ...}`

Rule:
- SDK methods should return `Result[...]`.
- Entry boundary may emit envelope for IPC/API compatibility.

## 3. Data Structures

Defined in `runtime.py`:
- `ErrorDetail`
- `OkEnvelope`
- `ErrEnvelope`
- `Envelope = OkEnvelope | ErrEnvelope`
- `EventMeta`, `EventHandler`, `HookMeta`

## 4. API Contracts

### PluginConfig (async-only)
- `dump(timeout=5.0) -> Result[Mapping[str, Any], Exception]`
- `get(path, default=None, timeout=5.0) -> Result[Any, Exception]`
- `require(path, timeout=5.0) -> Result[Any, Exception]`
- `set(path, value, timeout=5.0) -> Result[None, Exception]`
- `update(patch, timeout=5.0) -> Result[Mapping[str, Any], Exception]`
- `get_section(path, timeout=5.0) -> Result[Mapping[str, Any], Exception]`

Boundary rules:
- `timeout > 0`
- dotted path syntax is validated in implementation

### Plugins (async-only)
- `call_entry(entry_ref, args=None, timeout=10.0)`
- `call_event(event_ref, args=None, timeout=10.0)`
- `list(timeout=5.0)`
- `require(plugin_id, timeout=5.0)`

Boundary rules:
- `entry_ref` format: `<plugin_id>:<entry_id>`
- `event_ref` format: `<plugin_id>:<event_type>:<event_id>`
- `timeout > 0`

### PluginRouter (dynamic entry)
- `add_entry(entry_id, handler, ..., replace=False) -> Result[bool, Exception]`
- `remove_entry(entry_id) -> Result[bool, Exception]`
- `list_entries() -> Result[list[EventMeta], Exception]`

Boundary rules:
- `entry_id` non-empty
- duplicate id without `replace=True` should fail

### Memory/System/Storage
- all async-only methods return `Result`
- input validation and transport/storage failures surface as `Err(Exception)`

## 5. Decorator Contracts

Defined in `decorators.py` with full signatures:
- `neko_plugin`
- `plugin_entry`
- `lifecycle`
- `message`
- `timer_interval`
- `custom_event`
- hook family

Boundary rules include:
- `plugin_entry(model_validate=True)` means runtime validation required
- `timer_interval(seconds)` expects positive integer
- `hook(timing)` in `{before, after, around, replace}`

## 6. Base Class Contract

Defined in `base.py`:
- `NekoPluginBase(ctx)`
- `get_input_schema()`
- `include_router()/exclude_router()`
- `enable_file_logging(...)`

Runtime wiring for `config/plugins/store/db/state` is implementation-phase work.

## 7. Implementation Phase Order

1. `shared/core/config.py`
2. `shared/core/plugins.py`
3. `shared/core/router.py`
4. `shared/runtime/memory.py` + `shared/runtime/system_info.py`
5. `shared/storage/*`
6. `plugin/runtime.py` wiring from `shared/*`
