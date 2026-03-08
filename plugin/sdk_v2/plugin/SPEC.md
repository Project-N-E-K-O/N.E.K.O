# sdk_v2.plugin SPEC (SDD Contract)

## Scope

`sdk_v2.plugin` is the standard plugin-facing API surface.
It is the default facade for normal plugin development.

## Surface

- `base.py`: `NekoPluginBase`, `PluginMeta`
- `decorators.py`: plugin entry / event / hook decorators
- `runtime.py`: config, plugin calls, router, result/envelope model, runtime tools

## Error Model

- primary model: `Result` (`Ok` / `Err`)
- boundary helper: `ok(...)` / `fail(...)` envelope helpers
- runtime helpers: `must`, `unwrap`, `bind_result`, `map_err_result`, etc.

## API Contracts

- `PluginConfig`: async config access/update facade
- `Plugins`: async cross-plugin call facade
- `PluginRouter`: dynamic entry registration facade
- runtime helpers: call-chain, memory, system, storage contracts

## Decorator Contracts

- `neko_plugin`
- `plugin_entry`
- `lifecycle`
- `message`
- `timer_interval`
- `custom_event`
- hook family

## Base Contract

- `NekoPluginBase(ctx)`
- `get_input_schema()`
- `include_router()/exclude_router()`
- `enable_file_logging(...)`

## Layering Rules

- outer `plugin` facade stays stable
- shared capabilities come from `shared/*`
- internal models and composition may sink to `public/*`
