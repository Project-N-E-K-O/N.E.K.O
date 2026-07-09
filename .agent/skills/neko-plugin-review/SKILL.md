---
name: neko-plugin-review
description: Review N.E.K.O plugin work for boundary violations, plugin identity drift, plugin.toml contract errors, SDK misuse, missing design brief alignment, UI permission risks, lifecycle/runtime mistakes, and debugging or validation gaps. Use when reviewing a plugin, plugin PR, plugin diff, or generated plugin before accepting it.
---

# N.E.K.O Plugin Review

Review plugin work as an integration contract, not just code style.

Read first:

- `../neko-plugin/references/execution-boundary.md`
- `../neko-plugin/references/core-plugin-contract.md`
- `../neko-plugin/references/plugin-checks-and-tests.md`
- `../neko-plugin/references/plugin-system-surface-map.md` when SDK/API capability is unclear

## Process

1. Identify the target `plugin_id` and expected write workspace.
2. Check whether the diff edited anything outside `plugin/plugins/<plugin_id>/`.
3. Check Identity Lock: folder, `[plugin].id`, `[plugin].name`, `[plugin].entry`, and main class must express one plugin identity.
4. Check `plugin.toml` against the Manifest Contract.
5. Check that plugin purpose, entries, UI surfaces, runtime settings, and docs/design brief agree.
6. Check for SDK misuse: prefer `plugin.sdk.plugin`, `plugin.sdk.extension`, or `plugin.sdk.adapter`; do not import from `plugin.sdk.shared` in normal plugin code.
7. Check UI permissions and runtime settings for minimum necessary scope.
8. Check validation: CLI, focused tests, or manual trigger path.

## Report

Lead with findings ordered by risk. Include file paths and concise reasoning.

Use these labels:

- Boundary
- Identity
- Manifest
- SDK
- UI/Permissions
- Runtime/Lifecycle
- Validation

If there are no findings, say so and name any residual test gaps.
