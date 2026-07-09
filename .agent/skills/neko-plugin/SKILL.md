---
name: neko-plugin
description: Route N.E.K.O plugin work to the right plugin skill. Use when the user asks to create, modify, review, run, debug, inspect, document, or reason about N.E.K.O plugins, plugin.toml, plugin SDK usage, plugin UI surfaces, plugin CLI tooling, or plugin runtime behavior.
---

# N.E.K.O Plugin

Use this as the top-level entry point for N.E.K.O plugin work. It gives the hard defaults; load the referenced files for details before editing.

## Non-Negotiables

- Read `references/execution-boundary.md` before any plugin edit.
- Default writable area is only `plugin/plugins/<plugin_id>/`.
- Plugin workspaces live under repository-relative `plugin/plugins/<plugin_id>/`; read `references/core-plugin-contract.md` for the canonical file tree.
- Docs, tests, examples, indexes, and platform source are read context; platform edits require explicit user request or confirmation.
- Create standard plugin scaffolds with `uv run neko-plugin init ...`; do not hand-create the initial plugin directory, `plugin.toml`, or entry class.
- Prefer public SDK facades: `plugin.sdk.plugin`, `plugin.sdk.extension`, `plugin.sdk.adapter`. Do not add new `plugin.sdk.shared` imports in plugin workspace code.
- Runtime-triggered `@plugin_entry` handlers must be `async def`; `plugin_runtime.auto_start=false` is manual-start, not disabled or import-safe.

## Package Types

Choose package type from the manifest/SDK contract, not from feature vibes:

- `plugin`: default independent feature. It can include entries, lifecycle/background work, timers, message handlers, UI, storage/settings, cross-plugin calls, and ordinary external API/device integrations.
- `extension`: adds entries/hooks to an existing host plugin and requires `[plugin.host]`.
- `adapter`: bridges an external protocol or request stream into N.E.K.O plugin calls. Calling an external service is not enough to make something an adapter.
- `script`: present in core type literals but not scaffolded by `uv run neko-plugin init`; verify runtime support before using it.

## Route

- Create a new plugin or modify plugin behavior: use `neko-plugin-authoring`.
- Review plugin changes, manifests, boundaries, or SDK usage: use `neko-plugin-review`.
- Run, inspect, validate, package, start, stop, or debug a plugin: use `neko-plugin-debugging`.
- Need system capability awareness before designing: read `references/plugin-system-surface-map.md`.
- Creating a plugin from scratch: read `references/plugin-creation-workflow.md`.

## Reference Map

- `references/core-plugin-contract.md`: identity lock, `plugin.toml`, entry imports, package type, capabilities.
- `references/plugin-creation-workflow.md`: minimum questions, design brief, scaffold commands.
- `references/plugin-system-surface-map.md`: SDK/API capability index; check before inventing abstractions.
- `references/plugin-cli-and-debugging.md`: CLI usage and runtime/debug workflow.
- `references/plugin-checks-and-tests.md`: plugin-facing checks and tests.

Do not answer plugin authoring questions from memory when the references point to repo code. Read the relevant docs, tests, indexes, and source first; keep writes local.
