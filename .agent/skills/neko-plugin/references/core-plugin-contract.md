# Core Plugin Contract

This is the hard reference for N.E.K.O plugin fundamentals.

## Identity Lock

Settle `plugin_id` before implementation.

Start from the two files that bind identity:

- `plugin/plugins/<plugin_id>/plugin.toml` declares `[plugin].id`, `[plugin].name`, and `[plugin].entry`.
- The Python entry module referenced by `[plugin].entry`, usually `plugin/plugins/<plugin_id>/__init__.py`, defines the main entry class.

Then make these five surfaces express the same plugin concept:

1. folder name
2. `[plugin].id`
3. `[plugin].name`
4. `[plugin].entry`
5. main entry class

They do not need to be identical strings. They must point to one coherent plugin identity: the folder and `[plugin].id` use the stable machine id, `[plugin].name` is the human display name for that same concept, `[plugin].entry` points at the entry module/class, and the main class names the same concept in PascalCase.

Default derivation:

- `plugin_id`: stable snake_case id
- avoid a redundant `_plugin` suffix unless "plugin" is part of the actual product concept; the CLI appends `Plugin` to the class name
- folder: `plugin/plugins/<plugin_id>/`
- `[plugin].id`: `<plugin_id>`
- `[plugin].name`: human display name for the same concept
- main class: PascalCase concept + `Plugin`
- `[plugin].entry`: use the CLI-generated canonical entry path, `plugin.plugins.<plugin_id>:<MainClass>`

If several ids are plausible, ask. Otherwise derive the id and show the Identity Lock before implementation.

## Plugin Workspace Tree

A plugin workspace is always repository-relative:

```text
plugin/plugins/<plugin_id>/
```

Do not use `/plugins`, repo-root `plugins/`, or ad-hoc plugin directories. Treat `plugin/plugins/<plugin_id>/` as the plugin's editable world.

The standard CLI scaffold creates this shape:

```text
plugin/plugins/<plugin_id>/
├── plugin.toml
├── __init__.py
├── pyproject.toml
├── README.md
├── tests/
│   └── test_smoke.py
├── .gitignore
└── .vscode/
    ├── settings.json
    └── tasks.json
```

If requested by CLI options, the scaffold may also create:

```text
plugin/plugins/<plugin_id>/
└── .github/
    └── workflows/
        ├── verify.yml
        └── release.yml
```

Agent-authored design notes belong inside the workspace:

```text
plugin/plugins/<plugin_id>/DESIGN.md
```

Capability-specific additions also stay inside the workspace:

- `ui/` for hosted TSX surfaces
- `static/` for static UI assets
- `docs/` or `doc/` for plugin-owned user/developer docs
- `i18n/` for plugin-owned locale files
- `vendor/` for plugin-local Python runtime dependencies when `pyproject.toml` declares external dependencies
- extra Python modules/packages for plugin-local helpers

Required minimum:

- `plugin.toml`
- the entry module referenced by `[plugin].entry`, usually `__init__.py` in the CLI scaffold

Generated support files are plugin-owned when they are under `plugin/plugins/<plugin_id>/`, but create or edit them only when useful for the task. Do not move plugin helpers outside the workspace to share code; platform/shared code changes require explicit user request or confirmation.

## Manifest Contract

`plugin.toml` is a strict runtime contract.

Required:

- top-level `[plugin]`
- `[plugin].id`
- `[plugin].name`
- `[plugin].entry`

Core rules:

- `id` should match the folder name and use the locked `plugin_id`.
- `entry` must be `module.path:ClassName` with no leading/trailing whitespace.
- `type` defaults to `plugin`.
- `type = "extension"` requires `[plugin.host]`.
- non-extension plugins must not declare `[plugin.host]`.
- `version` should follow `x.y.z...`.
- `keywords`, if present, must be a list of non-empty strings.
- `passive`, if present, must be a TOML boolean, not a string.
- `[plugin_runtime].enabled` and `auto_start` must be TOML booleans.
- `[plugin_runtime].timeout` must satisfy `0 < timeout <= 300`.
- `[plugin_runtime].startup_failure` must be `warn`, `fail`, or `ignore`.
- `enabled = false` disables runtime loading; `auto_start = false` only makes the plugin manual-start. It may still be imported and statically scanned.
- `passive = true` affects discovery/agent dispatch, not process startup or event handling.
- UI permissions must be minimum necessary; do not add `config:write` unless the UI writes config.
- Custom business sections are allowed, but do not invent platform sections without checking schema/runtime support.
- New standard plugins should get their initial `plugin.toml` from `uv run neko-plugin init`, then be edited only as needed.

Primary source files:

- `plugin/config/schema.py`
- `plugin/config/plugin_toml_semantics.py`
- `docs/plugins/plugin-toml.md`
- existing `plugin/plugins/*/plugin.toml`

## Entry Contract

Normal plugin code should use public SDK imports, usually:

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, lifecycle, Ok, Err
```

Use public SDK facades for plugin code: `plugin.sdk.plugin`, `plugin.sdk.extension`, or `plugin.sdk.adapter`. Treat `plugin.sdk.shared` as internal SDK implementation. Do not add new `plugin.sdk.shared` imports in plugin workspace code; if a needed symbol is not exposed by a public facade, stop and escalate instead of reaching into shared internals.

Entry rules:

- Runtime-triggered `@plugin_entry` handlers must be `async def`; sync entries can be discovered but fail when triggered.
- Return `Ok(payload)` / `Err(SdkError(...))` for normal flow; uncaught exceptions become runtime errors.
- `input_schema` and `params` are mutually exclusive.
- `llm_result_fields`, `llm_result_model`, and `fields` are mutually exclusive.
- `input_schema` describes exposed shape; Pydantic runtime validation requires `params` or an inferred single model parameter.
- Entry timeout can be set on `@plugin_entry(timeout=...)`; `timeout <= 0` disables the timeout.
- Entry names, descriptions, schemas, and return payloads should match the plugin purpose and be traceable from `plugin.toml` and `DESIGN.md`.

## Package Type

Choose the package type from the manifest/SDK contract.

- `plugin`: default for independent features. Use it for user-callable entries, background listeners, timers, hosted/static UI, state/settings, cross-plugin calls, and ordinary external API/device integrations controlled from N.E.K.O.
- `extension`: only for adding entries or hooks to an existing host plugin without modifying that host. It uses `plugin.sdk.extension`, runs injected into the host plugin process, and requires `[plugin.host]`.
- `adapter`: only for bridging an external protocol or request stream into N.E.K.O plugin calls. It uses `plugin.sdk.adapter` plus adapter/gateway contracts. Do not choose adapter merely because the plugin calls an external service.
- `script`: exists in the core type literal but is not scaffolded by `uv run neko-plugin init`; do not use it for new plugin work unless the user explicitly asks and runtime support has been verified.

Capabilities are selected after package type:

- callable entry: `@plugin_entry`
- lifecycle/background behavior: `@lifecycle`
- scheduled behavior: `@timer_interval(id=..., seconds>0)`
- host message reaction: `@message(id=...)`
- UI surface: `[plugin.ui]` plus `[[plugin.ui.panel]]`, `[[plugin.ui.guide]]`, or `[[plugin.ui.docs]]`; choose the surface kind separately from the rendering mode
- state/config: `[plugin.store]`, `PluginStore`, `PluginSettings`, config profiles
- protocol gateway: adapter gateway components

## UI Surface Modes

Do not collapse UI kind and UI mode.

Surface kind controls where the surface appears:

- `[[plugin.ui.panel]]`: plugin management or dashboard surface.
- `[[plugin.ui.guide]]`: guide or quickstart surface.
- `[[plugin.ui.docs]]`: documentation surface.

Rendering mode controls how `entry` is loaded:

- `hosted-tsx`: interactive TSX/JSX surface, usually `entry = "ui/panel.tsx"`. Use for forms, buttons, tables, config/state views, and UI actions. Add only the permissions it needs, such as `state:read`, `config:read`, `config:write`, or `action:call`.
- `markdown`: read-only Markdown/MDX surface, usually `entry = "docs/quickstart.md"`. Use for simple docs and guides; it should not rely on UI actions.
- `static`: legacy standalone HTML surface, usually `entry = "static/index.html"`. Use when the plugin already owns a custom HTML/CSS/JS page or needs full iframe control.

If `mode` is omitted, the platform infers it from `entry`: `.tsx`/`.jsx` -> `hosted-tsx`, `.md`/`.mdx` -> `markdown`, `.html`/`.htm` -> `static`. `auto` exists for inference/compatibility; do not choose it as the default authoring mode.

## Runtime and UI Semantics

- Do not put expensive imports, network calls, or irreversible side effects at module top level; the parent process may import the entry module for metadata scanning even when runtime auto-start is false.
- Config updates cannot modify `plugin.id` or `plugin.entry`; treat them as identity fields.
- Hosted TSX action calls require an action-capable surface, `action:call` permission, a running plugin, and an action exposed by plugin UI context that maps to a real `@plugin_entry`.
- Use `push_message(parts=..., visibility=..., ai_behavior=...)` for new message output; legacy `message_type`, `delivery`, and `reply` are compatibility paths.
- `self.bus` is a read/watch facade over host state, not a general publish bus.
- Python runtime dependencies belong in plugin-local `pyproject.toml [project].dependencies` and `vendor/`; do not add `requirements.txt`. Extensions must not declare external Python runtime dependencies.
