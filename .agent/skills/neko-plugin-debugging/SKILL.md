---
name: neko-plugin-debugging
description: Run, inspect, validate, package, start, stop, trigger, or debug N.E.K.O plugins using the plugin CLI, runtime tools, logs, checks, tests, and existing debugging paths. Use when the user reports a plugin failing to load, start, validate, package, expose entries, render UI, or behave correctly at runtime.
---

# N.E.K.O Plugin Debugging

Read these first:

- `../neko-plugin/references/execution-boundary.md`
- `../neko-plugin/references/plugin-cli-and-debugging.md`
- `../neko-plugin/references/plugin-checks-and-tests.md`

If the issue touches identity, manifest, entry decorators, package type, runtime flags, UI permissions, or SDK imports, also read `../neko-plugin/references/core-plugin-contract.md`.

## Workflow

1. Identify `plugin_id`, write workspace, and whether the plugin is running, disabled, manual-start, or load-failed.
2. Inspect `plugin.toml`, entry class, local tests, plugin logs, and relevant `DESIGN.md`.
3. Run the smallest plugin-facing check first: usually `uv run neko-plugin check <plugin_id>`.
4. For entry/runtime failures, remember `@plugin_entry` handlers must be `async def`; reproduce with a trigger/runtime test when check is not enough.
5. For UI action failures, verify manifest permissions, surface availability, plugin running state, and exposed UI-context actions.
6. Fix only inside `plugin/plugins/<plugin_id>/` unless the user explicitly confirms platform work.
7. Re-run the narrowest check or test that covers the failure, then broaden only if the touched behavior crosses subsystem boundaries.

## Output

Report:

- root cause or current best diagnosis
- files changed
- checks/tests run
- whether any platform edit was needed, avoided, or still requires escalation
