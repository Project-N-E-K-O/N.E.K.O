---
name: neko-plugin-authoring
description: Create or modify N.E.K.O plugins inside the plugin workspace. Use when the user asks to create a plugin, add or change plugin behavior, edit plugin.toml, add plugin entries, use the plugin SDK, add plugin UI surfaces, configure plugin state/settings, or align a plugin with its design brief. Enforce the plugin write boundary and escalate before platform-layer edits.
---

# N.E.K.O Plugin Authoring

You are working inside a plugin workspace. Read these first:

- `../neko-plugin/references/execution-boundary.md`
- `../neko-plugin/references/core-plugin-contract.md`

For new plugins, also read `../neko-plugin/references/plugin-creation-workflow.md`.

For system capabilities, read `../neko-plugin/references/plugin-system-surface-map.md` before inventing new abstractions.

## New Plugin Workflow

1. Ask the Minimum Question Set from `plugin-creation-workflow.md` if the answers are not already clear.
2. Derive and show the Identity Lock before implementation.
3. Infer the package type and capabilities, then ask only required Risk Follow-ups.
4. Present a short Plugin Design Brief.
5. After confirmation, create the standard scaffold with `uv run neko-plugin init ...` from `plugin-creation-workflow.md`.
6. Read the generated files, then create `plugin/plugins/<plugin_id>/DESIGN.md`.
7. Implement only inside that generated workspace.
8. Update `plugin.toml` only within the Core Plugin Contract.
9. Use existing plugins as examples; prefer public SDK imports over `plugin.sdk.shared`.
10. Validate with focused CLI checks or tests from `../neko-plugin/references/plugin-checks-and-tests.md`.

## Existing Plugin Workflow

1. Identify `plugin_id` and write workspace.
2. Read the plugin's `DESIGN.md` if present, then `plugin.toml`, entry class, nearby files, and relevant tests/docs.
3. Confirm the requested change fits the existing plugin purpose and out-of-scope boundaries.
4. Make the smallest local change inside `plugin/plugins/<plugin_id>/`.
5. If the change needs platform-layer edits, stop and escalate before editing.

## Output

Report:

- Identity Lock or target plugin.
- Files changed.
- Whether any out-of-workspace edit was requested or avoided.
- Validation performed and remaining gaps.
