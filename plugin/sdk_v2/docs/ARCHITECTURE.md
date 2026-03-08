# SDK v2 Architecture (Draft)

## Plugin-Facing Surfaces

- `plugin.sdk_v2.plugin`
- `plugin.sdk_v2.extension`
- `plugin.sdk_v2.adapter`
- `plugin.sdk_v2.shared` (advanced)

## Guidance

- Most plugin authors should start with one of: `plugin` / `extension` / `adapter`.
- `plugin` does not re-export `extension`/`adapter`; choose the surface explicitly.
- `shared` provides lower-level reusable building blocks and may evolve faster.

## Internal Layers (implementation detail)

- `public/*`: type-oriented composition layers
- `shared/*`: reusable core/bus/storage/runtime/transport/models/compat modules
