# sdk_v2.extension SPEC (SDD Contract)

## Scope

`sdk_v2.extension` is the narrower extension-facing API surface.
It preserves the same async-first and `Result`-first semantics as `plugin`, but
with a smaller capability boundary.


## Recommended Imports

- `from plugin.sdk_v2.extension import ...` for extension development
- prefer `extension` facade before reaching for `shared/*`

## Surface

- `base.py`: `ExtensionMeta`, `NekoExtensionBase`
- `decorators.py`: extension entry / hook decorators
- `runtime.py`: config, router, transport, result model, runtime tools

## Error Model

- primary model: `Result` (`Ok` / `Err`)
- boundary helper: `ok(...)` / `fail(...)`
- shared error/version layer comes from `shared/*`

## API Contracts

- `ExtensionRuntime`: config/router/transport contract bundle
- runtime helpers: call-chain helpers for extension-safe scenarios

## Decorator Contracts

- `extension_entry`
- `extension_hook`
- `extension.entry(...)`
- `extension.hook(...)`

## Base Contract

- `NekoExtensionBase`
- `ExtensionMeta`

## Layering Rules

- outer `extension` facade stays stable
- extension depends only on lower shared layers
- internal extension composition may sink to `public/extension/*`
