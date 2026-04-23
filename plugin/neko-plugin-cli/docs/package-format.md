# N.E.K.O Plugin Package Format

## Overview

`neko-plugin-cli` defines two package types:

- `.neko-plugin`: single plugin package
- `.neko-bundle`: multi-plugin bundle package

The two package types share the same internal directory layout. Their semantic difference is determined by:

- the file extension
- `package_type` in `manifest.toml`

Both package types are standard ZIP archives and must remain compatible with normal archive tools.

## Design Goals

- Human-readable package metadata
- Simple implementation in Python
- Compatible with normal unzip tools
- Unified reader/writer for plugin package and bundle package
- Optional trust/signature support without blocking basic use

## CLI Entry

`neko-plugin-cli` now uses a single CLI entry:

```bash
uv run python plugin/neko-plugin-cli/cli.py <command> ...
```

Current commands:

- `pack`
- `inspect`
- `verify`
- `unpack`
- `analyze`

Examples:

```bash
uv run python plugin/neko-plugin-cli/cli.py pack qq_auto_reply
uv run python plugin/neko-plugin-cli/cli.py inspect qq_auto_reply.neko-plugin
uv run python plugin/neko-plugin-cli/cli.py verify qq_auto_reply.neko-plugin
uv run python plugin/neko-plugin-cli/cli.py unpack qq_auto_reply.neko-plugin
uv run python plugin/neko-plugin-cli/cli.py analyze qq_auto_reply mijia
```

## Archive Layout

```text
package.neko-plugin / package.neko-bundle
├── manifest.toml
├── metadata.toml
├── sign.toml              # optional
└── payload/
    ├── plugins/
    │   ├── <plugin_id>/
    │   │   ├── plugin.toml
    │   │   ├── pyproject.toml
    │   │   └── ...
    │   └── ...
    └── profiles/
        ├── default.toml
        └── ...
```

## Top-Level Files

### `manifest.toml`

Primary package descriptor. This file is required.

Responsibilities:

- identify package type
- describe package identity and version
- provide lightweight package-level display metadata
- declare included plugins for bundle packages when needed

This file should stay small and stable.

### `metadata.toml`

Supplementary package metadata. This file is recommended.

Responsibilities:

- payload hash
- source information

This file must not carry core loading rules that are required for basic extraction.

### `sign.toml`

Optional trust/signature metadata.

Responsibilities:

- declare whether the package is signed
- identify signer and algorithm
- carry signature material for manifest or payload hashes

Packages without `sign.toml` are treated as unsigned, not invalid.

## Payload Layout

### `payload/plugins/`

Contains packaged plugin directories.

Each plugin directory should be restorable into a runtime plugin directory. In other words, it should contain the plugin runtime files, not only configuration fragments.

Each plugin directory must contain:

- `plugin.toml`

Recommended:

- `pyproject.toml`
- plugin source files
- static assets
- runtime resources that belong to the distributed plugin

Must not include:

- `__pycache__/`
- `.pytest_cache/`
- `.mypy_cache/`
- `.venv/`
- `dist/`
- `build/`
- other temporary or development-only artifacts

### `payload/profiles/`

Contains extracted package profiles.

Profiles are the portable configuration layer for distribution. This replaces a separate `configs/` directory in the package format.

Profiles may define:

- enabled plugins
- per-plugin runtime flags
- per-plugin config values

Current lightweight rule for single-plugin packing:

- `[plugin_runtime]` contributes runtime flags such as `auto_start`
- the top-level table whose name equals the plugin id is migrated into the default profile as plugin config
- other metadata tables stay in `plugin.toml`

## Package Type Rules

### `.neko-plugin`

Rules:

- file extension should be `.neko-plugin`
- `manifest.toml` must declare `package_type = "plugin"`
- `payload/plugins/` must contain exactly one plugin directory

### `.neko-bundle`

Rules:

- file extension should be `.neko-bundle`
- `manifest.toml` must declare `package_type = "bundle"`
- `payload/plugins/` must contain one or more plugin directories
- bundle plugin list should be declared in `manifest.toml`

## `manifest.toml` Schema

### Common Fields

```toml
schema_version = "1.0"
package_type = "plugin" # or "bundle"

id = "qq_auto_reply"
package_name = "QQ 自动回复"
version = "0.2.0"
package_description = "读取 QQ 消息并根据权限自动回复。"
```

### Single Plugin Package Example

```toml
schema_version = "1.0"
package_type = "plugin"

id = "qq_auto_reply"
package_name = "QQ 自动回复"
version = "0.2.0"
package_description = "读取 QQ 消息并根据权限自动回复。"
```

### Bundle Package Example

```toml
schema_version = "1.0"
package_type = "bundle"

id = "streaming_suite"
package_name = "Streaming Suite"
version = "1.0.0"
package_description = "适合直播场景的插件整合包。"

[[plugins]]
id = "qq_auto_reply"
path = "payload/plugins/qq_auto_reply"

[[plugins]]
id = "mijia"
path = "payload/plugins/mijia"
```

## `metadata.toml` Schema

Suggested fields:

```toml
[payload]
hash_algorithm = "sha256"
hash = "..."

[source]
kind = "local"
path = "plugin/plugins/qq_auto_reply"
```

Notes:

- `payload.hash` should be computed over normalized `payload/` contents, not over the entire zip file
- metadata is allowed to evolve more freely than manifest
- `package_name` and `package_description` are package summary fields, not a replacement for the plugin's own `plugin.toml`

## `sign.toml` Schema

Suggested fields:

```toml
signed = true
algorithm = "ed25519"
signer = "neko-official"
public_key_id = "official-key-1"

[target]
payload_hash = "sha256:..."

[signature]
value = "base64:..."
```

## Validation Rules

Minimal validation:

1. archive must contain `manifest.toml`
2. archive must contain `payload/plugins/`
3. each packaged plugin directory must contain `plugin.toml`
4. `package_type = "plugin"` requires exactly one plugin directory
5. `package_type = "bundle"` requires at least one plugin directory

Recommended validation:

1. `metadata.toml` exists
2. bundle `[[plugins]]` entries match packaged plugin directory names
3. `metadata.payload.hash` matches computed payload hash
4. each packaged plugin directory contains `plugin.toml`

Optional trust validation:

1. `sign.toml` exists
2. `target.payload_hash` matches `metadata.toml`
3. signature verifies against the configured public key

## Packaging Pipeline Guidance

Recommended pipeline for a single plugin:

1. Read plugin source directory
2. Parse `plugin.toml`
3. Optionally parse `pyproject.toml`
4. Extract plugin profiles into `payload/profiles/`
5. Copy runtime plugin files into `payload/plugins/<plugin_id>/`
6. Generate `manifest.toml`
7. Generate `metadata.toml`
8. Optionally generate `sign.toml`
9. Export ZIP archive with `.neko-plugin` extension

Current implementation notes:

- single-plugin packing only accepts `package_type = "plugin"`
- unpack verifies `metadata.toml` payload hash when metadata exists
- unpack conflict handling currently supports `rename` and `fail`

Recommended pipeline for a bundle:

1. Collect multiple plugin sources or `.neko-plugin` inputs
2. Build unified `payload/plugins/`
3. Build shared `payload/profiles/`
4. Generate bundle `manifest.toml`
5. Generate bundle `metadata.toml`
6. Optionally generate `sign.toml`
7. Export ZIP archive with `.neko-bundle` extension

## Status

This document is an initial draft for `neko-plugin-cli`.

Current intent:

- stable enough for implementation scaffolding
- still open to adjustment before public compatibility guarantees are made
