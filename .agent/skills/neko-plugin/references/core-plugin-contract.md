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
- `type = "extension"` is deprecated compatibility metadata and requires `[plugin.host]`; the only loader-compatible legacy entry is a `PluginRouter` using `@plugin_entry`. Historical `NekoExtensionBase` / extension-specific decorator metadata is not consumed by the loader. Never choose extension for a new package.
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

## I18n Contract

Use one plugin-owned locale source for plugin text.

- Plugin metadata, entries, and actions use `tr()` references plus `[plugin.i18n]` locale files. This includes display names, descriptions, UI action labels, confirmation text, and other registered Python-side user-facing strings.
- Hosted TSX visible copy uses `props.t()` or `useI18n().t()` against the same plugin i18n messages. Do not hand-roll per-component locale dictionaries in TSX when the text can live in the plugin locale files.

## Entry Contract

Normal plugin code should use public SDK imports, usually:

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, lifecycle, Ok, Err
```

Use public SDK facades for plugin code: `plugin.sdk.plugin` or `plugin.sdk.adapter`. `plugin.sdk.extension` is deprecated and may be used only while maintaining an existing extension. Treat `plugin.sdk.shared` as internal SDK implementation. Do not add new `plugin.sdk.shared` imports in plugin workspace code; if a needed symbol is not exposed by a public facade, stop and escalate instead of reaching into shared internals.

Entry rules:

- Runtime-triggered `@plugin_entry` handlers must be `async def`; sync entries can be discovered but fail when triggered.
- Return `Ok(payload)` / `Err(SdkError(...))` for normal flow; uncaught exceptions become runtime errors.
- `input_schema` and `params` are mutually exclusive.
- `llm_result_fields`, `llm_result_model`, and `fields` are mutually exclusive.
- `input_schema` describes exposed shape; Pydantic runtime validation requires `params` or an inferred single model parameter.
- Entry timeout can be set on `@plugin_entry(timeout=...)`; `timeout <= 0` disables the timeout.
- Entry names, descriptions, schemas, and return payloads should match the plugin purpose and be traceable from `plugin.toml` and `DESIGN.md`.

## Router Contract

Routers are an organization mechanism for large plugins, not a default architecture choice.

Use `PluginRouter` only when the plugin's main entry file is becoming hard to maintain. As a rule of thumb, recommend routers when the project/plugin main module is over about 1k lines, has many entries split across distinct feature areas, or needs multiple contributors to own separate feature modules. Do not introduce routers for a minimal implementation or a plugin with only a few entries.

Router rules:

- Keep minimal plugins in the main entry class.
- Import router APIs from the public facade, usually `from plugin.sdk.plugin import PluginRouter, plugin_entry, Ok`.
- Register routers from the main plugin instance with `self.include_router(...)`.
- Treat router modules as plugin-local implementation details under `plugin/plugins/<plugin_id>/`, commonly `routers/`.
- Router entries still obey the Entry Contract: runtime-triggered `@plugin_entry` handlers must be `async def`, ids must be unique, and schemas/results should match plugin purpose.

## Hook Contract

Hooks are opt-in interception points for entry execution. Use them only when there is a concrete need to run logic before, after, around, or instead of another entry. Do not add hooks just to organize normal business logic; ordinary helper functions, entries, lifecycle hooks, or routers are simpler.

Hook rules:

- Use `before_entry`, `after_entry`, `around_entry`, `replace_entry`, or `hook` from the public SDK facade.
- Prefer the narrowest `target` entry id. Use `target="*"` only when every entry really needs the behavior.
- Keep hook behavior small and predictable: validation, auditing, timing, result shaping, compatibility shims, or extension-style interception.
- Be cautious with `replace_entry`; it changes the target entry's behavior and should be justified in the plugin design or review notes.
- Hooks do not replace lifecycle work. Use `@lifecycle` for startup, shutdown, reload, and config-change behavior.

## Package Type

Choose the package type from the manifest/SDK contract.

- `plugin`: default for independent features. Use it for user-callable entries, background listeners, timers, hosted/static UI, state/settings, cross-plugin calls, and ordinary external API/device integrations controlled from N.E.K.O.
- `extension`: deprecated compatibility type only. A legacy `[plugin.host]` package remains loadable only when its entry is a `PluginRouter` using `@plugin_entry`; `plugin.sdk.extension` is useful for diagnosing historical imports, not as the loader contract. Do not scaffold or recommend a new extension. Move ordinary feature code into a standard plugin or use an adapter for an external protocol.
- `adapter`: only for bridging an external protocol or request stream into N.E.K.O plugin calls. It uses `plugin.sdk.adapter` plus adapter/gateway contracts. Do not choose adapter merely because the plugin calls an external service.

Capabilities are selected after package type:

- callable entry: `@plugin_entry`
- large-plugin organization: `PluginRouter` plus `self.include_router(...)`; use only when the main plugin module is large or feature-split, not for minimal implementations
- entry interception: hook decorators such as `before_entry`, `after_entry`, `around_entry`, and `replace_entry`; use only for a concrete interception need
- lifecycle/background behavior: `@lifecycle`
- scheduled behavior: `@timer_interval(id=..., seconds>0)`
- host message reaction: `@message(id=...)`
- UI surface: `[plugin.ui]` plus `[[plugin.ui.panel]]`, `[[plugin.ui.guide]]`, or `[[plugin.ui.docs]]`; choose the surface kind separately from the rendering mode
- state/config: `[plugin.store]`, `PluginStore`, `PluginSettings`, config profiles
- protocol gateway: adapter gateway components

## UI Surface Modes

Current UI has three rendering modes. Default to Hosted TSX for new UI unless the surface is truly read-only documentation. Do not use Static legacy unless there is a concrete reason Hosted TSX or Markdown cannot serve the use case.

For implementation details, read `hosted-ui-authoring.md`. For the Hosted TSX component, hook, and type API, read `hosted-ui-api.md`.

Do not collapse UI kind and UI mode.

Surface kind controls where the surface appears:

- `[[plugin.ui.panel]]`: plugin management or dashboard surface.
- `[[plugin.ui.guide]]`: guide or quickstart surface.
- `[[plugin.ui.docs]]`: documentation surface.

Rendering mode controls how `entry` is loaded:

- `hosted-tsx`: primary mode for new plugin UI. Use TSX/JSX loaded by the Plugin Manager, usually `entry = "ui/panel.tsx"`. Choose it for interactive panels, settings, dashboards, forms, buttons, tables, state/config views, i18n-aware UI, and UI actions. Add only the permissions it needs, such as `state:read`, `config:read`, `config:write`, or `action:call`.
- `markdown`: read-only documentation mode. Use Markdown/MDX loaded by the Plugin Manager, usually `entry = "docs/quickstart.md"`. Choose it for quickstarts, guides, and reference docs. It should not require UI actions, custom scripts, or iframe behavior.
- `static`: legacy standalone HTML mode. Use HTML loaded in an iframe/static route, usually `entry = "static/index.html"`. Avoid it for new UI. Use it only when migrating an existing standalone page, when the plugin must preserve custom HTML/CSS/JS behavior, or when Hosted TSX cannot support a required browser/runtime capability.

If `mode` is omitted, the platform infers it from `entry`: `.tsx`/`.jsx` -> `hosted-tsx`, `.md`/`.mdx` -> `markdown`, `.html`/`.htm` -> `static`. `auto` exists for inference/compatibility; do not choose it as the default authoring mode.

## Runtime and UI Semantics

- Do not put expensive imports, network calls, or irreversible side effects at module top level; the parent process may import the entry module for metadata scanning even when runtime auto-start is false.
- Config updates cannot modify `plugin.id` or `plugin.entry`; treat them as identity fields.
- Hosted TSX action calls require an action-capable surface, `action:call` permission, a running plugin, and an action exposed by plugin UI context that maps to a real `@plugin_entry`.
- Use `push_message(parts=..., visibility=..., ai_behavior=...)` for message output. Legacy `message_type`, `description`, `content`, `binary_data`, `binary_url`, `mime`, `delivery`, `reply`, `unsafe`, and `fast_mode` are compatibility-only fields scheduled for removal in v0.9; do not introduce them.
- `self.bus` is a read/watch facade over host state, not a general publish bus. All five namespaces are readable, but only `messages`, `events`, and `lifecycle` support `watch()`; `conversations` and `memory` are read-only snapshots. Callable `filter(predicate)`, `where(predicate)`, and `sort(key=...)` are local-only; replayable watcher chains must use structured `filter(field=value, ...)` and `sort(by=...)`. Watcher subscriptions use only `on="add"`, `"del"`, or `"change"`.
- In async entries, use `await self.bus.memory.get(...)` for recent records and `await self.ctx.query_memory(...)` for semantic memory lookup. Do not recreate the removed high-level `self.memory`/`MemoryClient` facade.
- Python runtime dependencies belong in plugin-local `pyproject.toml [project].dependencies` and `vendor/`; do not add `requirements.txt`. Existing extensions in the compatibility window must not declare external Python runtime dependencies.
