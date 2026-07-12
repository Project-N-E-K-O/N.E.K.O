---
name: neko-plugin
description: Work on N.E.K.O plugins end to end. Use when the user asks to create, modify, review, run, debug, inspect, document, validate, package, or reason about N.E.K.O plugins, plugin.toml, plugin SDK usage, plugin UI surfaces, plugin CLI tooling, plugin runtime behavior, or plugin system contracts.
---

# N.E.K.O Plugin

Use this as the single entry point for N.E.K.O plugin work. Keep the workflow continuous: understand the plugin contract, edit when needed, validate the result, and self-review before reporting back.

## Non-Negotiables

- Read `references/execution-boundary.md` before any plugin edit.
- Read `references/core-plugin-contract.md` before changing plugin identity, manifest, entry code, SDK imports, UI surfaces, runtime settings, dependencies, or package type.
- Default writable area is only `plugin/plugins/<plugin_id>/`.
- Plugin workspaces live under repository-relative `plugin/plugins/<plugin_id>/`; read `references/core-plugin-contract.md` for the canonical file tree.
- Docs, tests, examples, indexes, and platform source are read context; platform edits require explicit user request or confirmation.
- Create standard plugin scaffolds with `uv run neko-plugin init ...`; do not hand-create the initial plugin directory, `plugin.toml`, or entry class.
- Prefer the public `plugin.sdk.plugin` and `plugin.sdk.adapter` facades. `plugin.sdk.extension` is deprecated and exists only to maintain already-installed extensions; do not select it for new work. Do not add new `plugin.sdk.shared` imports in plugin workspace code.
- Runtime-triggered `@plugin_entry` handlers must be `async def`; `plugin_runtime.auto_start=false` is manual-start, not disabled or import-safe.
- Prefer Hosted TSX for new interactive plugin UI, Markdown for read-only docs, and Static only for legacy standalone pages or unsupported browser/runtime needs.

## Package Types

Choose package type from the manifest/SDK contract, not from feature vibes:

- `plugin`: default independent feature. It can include entries, lifecycle/background work, timers, message handlers, UI, storage/settings, cross-plugin calls, and ordinary external API/device integrations.
- `extension`: deprecated compatibility type for already-existing host extensions. Do not scaffold a new extension; migrate the feature into the host as a normal plugin/router or use an adapter for an external protocol.
- `adapter`: bridges an external protocol or request stream into N.E.K.O plugin calls. Calling an external service is not enough to make something an adapter.

## Unified Workflow

### Create a Plugin

1. Read `references/plugin-creation-workflow.md`.
2. Ask only unresolved minimum questions: name, purpose, package type, first-version scope, and out of scope.
3. Derive and show the Identity Lock.
4. Present a short Plugin Design Brief.
5. After confirmation, run the standard `uv run neko-plugin init ...` command.
6. Read the generated files, add `plugin/plugins/<plugin_id>/DESIGN.md`, then implement only inside that workspace.
7. Validate with the focused checks from `references/plugin-checks-and-tests.md`.

### Modify an Existing Plugin

1. Identify `plugin_id` and workspace.
2. Read `DESIGN.md` if present, then `plugin.toml`, entry class, nearby files, and relevant tests/docs.
3. Check the request against plugin purpose and out-of-scope boundaries.
4. Make the smallest local change inside `plugin/plugins/<plugin_id>/`.
5. Run the narrowest plugin-facing check or test that covers the change.

### Debug a Plugin

1. Read `references/plugin-cli-and-debugging.md` and `references/plugin-checks-and-tests.md`.
2. Identify whether the plugin is running, disabled, manual-start, or load-failed.
3. Inspect `plugin.toml`, entry class, local tests, logs, and `DESIGN.md`.
4. Run `uv run neko-plugin check <plugin_id|plugin_path>` first unless the symptom clearly points elsewhere.
5. For entry/runtime failures, verify runtime-triggered entries are `async def`.
6. For UI failures, verify mode, surface permissions, running state, context/action exposure, and targeted Hosted TSX checks when relevant.
7. Fix only inside the plugin workspace unless escalation is confirmed.

### Review Plugin Work

Review plugin work as an integration contract, not only code style. Lead with findings ordered by risk.

Check:

- Boundary: diff stays inside `plugin/plugins/<plugin_id>/` unless escalation was requested.
- Identity: folder, `[plugin].id`, `[plugin].name`, `[plugin].entry`, and main class express one plugin concept.
- Manifest: `plugin.toml` follows the contract and uses minimum permissions.
- SDK: plugin code uses public facades, not new `plugin.sdk.shared` imports.
- UI/Permissions: surface kind and render mode are separate; Hosted TSX/Markdown/Static choice is justified.
- Runtime/Lifecycle: entries, imports, startup, shutdown, dependencies, and `auto_start` semantics are safe.
- Validation: CLI checks, focused tests, or manual trigger paths cover the changed behavior.

Use labels in review output: `Boundary`, `Identity`, `Manifest`, `SDK`, `UI/Permissions`, `Runtime/Lifecycle`, `Validation`.

### Escalate Platform Work

If a plugin goal cannot be achieved inside `plugin/plugins/<plugin_id>/`, stop before editing platform code and report:

1. The plugin-level goal.
2. Why the plugin workspace cannot support it.
3. The smallest out-of-bound change required.
4. Compatibility and test impact.

## Reference Map

- `references/core-plugin-contract.md`: identity lock, `plugin.toml`, entry imports, package type, capabilities.
- `references/execution-boundary.md`: write workspace, read context, and escalation rules.
- `references/plugin-creation-workflow.md`: minimum questions, design brief, scaffold commands.
- `references/plugin-system-surface-map.md`: SDK/API capability index; check before inventing abstractions.
- `references/plugin-cli-and-debugging.md`: CLI usage and runtime/debug workflow.
- `references/plugin-checks-and-tests.md`: plugin-facing checks and tests.
- `references/hosted-ui-authoring.md`: Hosted UI manifest, Python context/action, TSX runtime, source limits, and validation workflow.
- `references/hosted-ui-api.md`: Hosted TSX public component, hook, type, toast, confirm, and bridge API.

Do not answer plugin authoring questions from memory when the references point to repo code. Read the relevant docs, tests, indexes, and source first; keep writes local.
