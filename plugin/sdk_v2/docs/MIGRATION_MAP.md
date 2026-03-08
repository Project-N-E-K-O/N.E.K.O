# SDK v2 Migration Map (Draft)

## Recommended Imports

- Standard plugin:
  - `plugin.sdk_v2.plugin.*`
- Extension:
  - `plugin.sdk_v2.extension.*`
- Adapter:
  - `plugin.sdk_v2.adapter.*`
- Advanced/common building blocks:
  - `plugin.sdk_v2.shared.*`

## Notes

- `shared` is available for advanced scenarios; prefer flavor APIs when possible.
- `public/*` is an internal implementation layer and should not be treated as a supported developer import path.
