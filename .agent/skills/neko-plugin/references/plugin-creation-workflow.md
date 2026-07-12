# Plugin Creation Workflow

Use this when creating a new N.E.K.O plugin.

## Minimum Question Set

Ask these before designing a new plugin, unless the conversation already answered them.

1. What should this plugin be called?
2. What is this plugin for?
3. Which package type fits it?
   - Plugin: an independent feature; this is the default for tools, background work, timers, UI, and ordinary external service/device integrations.
   - Adapter: bridges an external protocol/request stream into N.E.K.O plugin calls.
4. What should the first version include?
   - callable entries, background/lifecycle work, timers, message reaction, UI, storage/settings, cross-plugin calls, external service/device connection, or protocol gateway.
5. What is out of scope for the first version?

`extension` is deprecated and is not a creation option. If a request sounds like an extension, design a normal plugin, put code in the existing host plugin (using `PluginRouter` when it is large), or select an adapter only when an external protocol is being bridged.

Keep questions in user-facing language. Use the answers to infer plugin architecture; do not ask the user to design individual entries first.

When deriving `plugin_id`, avoid a redundant `_plugin` suffix unless it is part of the real name. The CLI appends `Plugin` to the generated class name.

## Follow-ups

Ask Guidance Follow-ups only when the user's intent is vague and they need help choosing a direction.

Ask Risk Follow-ups when answers imply:

- external protocols or services
- credentials or secrets
- persistence, database, or state ownership
- UI permissions
- background work, auto-start, timers, or shutdown behavior
- migration constraints when maintaining an already-existing deprecated extension
- adapter/gateway behavior
- out-of-bound platform needs

## Design Brief

Before implementation, present a short Plugin Design Brief. After confirmation, create the plugin with the CLI, then save the brief as:

```text
plugin/plugins/<plugin_id>/DESIGN.md
```

Template:

```md
# <Plugin Name> Design Brief

## Identity Lock
- plugin_id:
- folder:
- name:
- entry:
- main class:

## Purpose

## Package Type and Capabilities
- package type:
- capabilities:
- inferred architecture:

## First Version Scope

## Out of Scope

## Inferred Technical Needs
- plugin.toml sections:
- SDK surfaces:
- UI surfaces:
- state/config:
- lifecycle/background work:
- external integrations:

## Read Context Plan

## Write Workspace
`plugin/plugins/<plugin_id>/`

## Risk Follow-ups
```

Keep `DESIGN.md` short. Do not use it as a changelog or API reference.

## Standard Scaffold

Create standard plugins through the CLI in the project uv environment. Do not hand-create the initial plugin directory, `plugin.toml`, or entry class.

For a normal plugin:

```bash
uv run neko-plugin init <plugin_id> --type plugin --name "<Plugin Name>" --no-interactive
```

For an adapter:

```bash
uv run neko-plugin init <plugin_id> --type adapter --name "<Plugin Name>" --no-interactive
```

Do not run `neko-plugin init --type extension`: Extension is deprecated and the CLI no longer scaffolds new extensions. Only the legacy `PluginRouter` + `@plugin_entry` package shape remains loader-compatible; historical facade-based packages must be converted while they are migrated.

The CLI owns the initial tree under `plugin/plugins/<plugin_id>/`. Expect at least `plugin.toml` and the entry module, and normally `__init__.py`, `pyproject.toml`, `README.md`, `tests/test_smoke.py`, `.gitignore`, and `.vscode/`. Add capability directories such as `ui/`, `static/`, `docs/`, `i18n/`, or `vendor/` only when the plugin actually needs them.

After CLI scaffolding:

1. Read the generated tree, `plugin.toml`, and entry class.
2. Check Identity Lock and Plugin Workspace Tree against the generated files.
3. Write `DESIGN.md` into `plugin/plugins/<plugin_id>/`.
4. Convert or keep runtime-triggered entries as `async def`.
5. Move expensive startup, network calls, and side effects out of module import time and into lifecycle/entry handlers.
6. Modify only inside the generated workspace.

## Implementation Gate

Do not implement until these are clear:

- Identity Lock
- Package Type and Capabilities
- Write Workspace
- First Version Scope
- Out of Scope
- unresolved Risk Follow-ups, if any
- standard scaffold command to run
- async entry plan for callable entries
- no top-level side effects beyond constants, imports, and cheap definitions
