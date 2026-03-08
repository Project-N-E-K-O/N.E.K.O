# sdk_v2.adapter SPEC (SDD Contract)

## Scope

`sdk_v2.adapter` is the adapter-facing API surface for protocol bridge and
gateway scenarios.

## Surface

- `base.py`: adapter base / config / context contracts
- `decorators.py`: adapter lifecycle / event decorators
- `runtime.py`: gateway runtime contracts, defaults, models, shared result layer
- `types.py`: transport and routing data types

## Error Model

- primary model: `Result` (`Ok` / `Err`)
- boundary helper: `ok(...)` / `fail(...)`
- gateway-specific errors/models remain explicit in runtime contracts

## API Contracts

- `AdapterBase`, `AdapterContext`, `AdapterConfig`
- `AdapterGatewayCore`
- default gateway collaborator contracts
- transport / routing / request / response models

## Decorator Contracts

- `on_adapter_event`
- `on_adapter_startup`
- `on_adapter_shutdown`
- `on_mcp_tool`
- `on_mcp_resource`
- `on_nonebot_message`

## Base Contract

- `AdapterBase(config, ctx)`
- `AdapterContext(...)`
- `NekoAdapterPlugin`

## Layering Rules

- outer `adapter` facade stays stable
- non-facade gateway detail may sink to `public/adapter/*`
- developer-facing imports should prefer `sdk_v2.adapter` / `sdk_v2.adapter.runtime`
