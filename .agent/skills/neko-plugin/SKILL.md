---
name: neko-plugin
description: Work on N.E.K.O plugins end to end. Use when the user asks to create, modify, review, run, debug, inspect, document, validate, package, or reason about N.E.K.O plugins, plugin.toml, plugin SDK usage, plugin UI surfaces, plugin CLI tooling, plugin runtime behavior, or plugin system contracts.
---

# N.E.K.O Plugin

Single entry point for N.E.K.O plugin work: understand the contract, edit,
validate, and self-review before reporting back. "The plugin directory" below
is the folder holding the plugin's `plugin.toml`; it is the only place you
write.

## Hard Constraints

One-line reasons are in the plugin's `AGENTS.md`; full reasons and the escalation
format in `references/execution-boundary.md`.

1. Do not edit N.E.K.O itself
2. Do not import host internals
3. Do not call N.E.K.O's local services directly
4. Do not use `print`
5. Do not log conversation text, secrets, or tokens
6. Do not hard-code paths
7. Do not block the event loop
8. Do not use `requirements.txt`
9. Do not commit `vendor/`, binaries, or files over 2 MiB
10. Do not use `eval`/`exec`/`pickle`/`shell=True`/`verify=False`

## Package Types

- `plugin`: the default. Entries, lifecycle, timers, message handlers, UI,
  storage, cross-plugin calls, and ordinary external API/device integrations.
- `adapter`: only for bridging an external protocol or request stream into
  N.E.K.O plugin calls. Calling an external service does not make an adapter.
- `extension` was removed; see the
  [migration guide](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/docs/plugins/migration-v0.9.md).

## CLI

From a N.E.K.O source checkout, `uv run neko-plugin <command> <plugin>`, where
`<plugin>` is an id under the checkout's plugin root or a path. Confirm
arguments with `--help`. A checkout (Route A) is the recommended way to
develop. Without one (desktop build, Route B) no `neko-plugin` command runs:
tell the user rather than guessing. App buttons (Start/Reload, Logs, Build
package in the Plugin studio) still work; they are not the CLI. To run checks,
ask the user whether to set up a checkout ("Setting Up Route A" in
`references/plugin-cli-and-debugging.md`) and wait for their consent.

| Command | Use |
|---|---|
| `init`, `setup-repo`, `create-repo` | create a plugin; add repo support files; create its GitHub repo |
| `check`, `check -r` | readiness; strict pre-release check (tests, package, hash) |
| `lint` | the pinned ruff check Market CI runs (`# noqa` is ignored) |
| `sync --clean` | rebuild `vendor/` from `pyproject.toml` |
| `precheck` | lint + sync + `check -r`, the Market CI gates |
| `build`, `analyze`, `publish` | package; bundle analysis; release |

Validation order: `check` → `lint` → `sync --clean` → `precheck`.
When the plugin has its own Git repository (outside the checkout's built-in
plugin folder), `check -r` and `precheck` fail on uncommitted changes. Do not
commit for the user unasked; report the actual result.

## Workflows

### Create a Plugin (only when no plugin exists yet)

If the current folder already has a `plugin.toml`, skip to Modify.

1. Read `references/plugin-creation-workflow.md`. Offer only `plugin` and
   `adapter` as package types.
2. Ask only the unresolved minimum: name, purpose, package type, first-version
   scope, out of scope. Show the Identity Lock and a short design brief.
3. After confirmation, scaffold with `neko-plugin init`; never hand-write the
   initial directory, `plugin.toml`, or entry class.
4. Add `DESIGN.md`, then implement inside the new plugin directory. Read
   `references/core-plugin-contract.md` before changing the manifest,
   identity, entries, or UI.
5. Validate in the order under CLI.

### Modify

1. Read `DESIGN.md` if present, then `plugin.toml`, the entry class, nearby
   files, and tests.
2. Check the request against the plugin's purpose and out-of-scope list.
3. Make the smallest change inside the plugin directory, then validate.

### Debug

1. Is the plugin running, stopped, manual-start, crashed, or load-failed?
   Read its log first.
2. Run `check` unless the symptom clearly points elsewhere.
3. Match the symptom against `references/runtime-model.md` (startup, loops,
   timers, reload) and `references/plugin-cli-and-debugging.md`.

### Review

Lead with findings ordered by risk, labelled `Boundary`, `Identity`,
`Manifest`, `SDK`, `UI/Permissions`, `Runtime/Lifecycle`, `Validation`:
writes stay in the plugin directory; identity fields agree; manifest uses
minimum permissions; only public SDK imports; UI mode is justified; lifecycle
and timers follow the runtime model; checks cover the change.

### Escalate

If the goal needs a host change, stop and follow the escalation section of
`references/execution-boundary.md`.

## Reference Map

- `references/runtime-model.md`: processes, event loops, startup/shutdown,
  timers, reload, crash behaviour. Read before any lifecycle or background work.
- `references/execution-boundary.md`: the hard constraints with reasons;
  allowed imports; escalation.
- `references/core-plugin-contract.md`: identity, `plugin.toml`, entries,
  routers, hooks, i18n, UI modes.
- `references/plugin-system-surface-map.md`: SDK capability index (paths,
  `push_message`, config, cross-plugin calls); check it before inventing
  abstractions.
- `references/dependencies-and-market.md`: `vendor/`, third-party packages,
  Market id/tag/size rules, what local checks do not prove.
- `references/plugin-creation-workflow.md`: questions, design brief, scaffold.
- `references/plugin-cli-and-debugging.md`, `references/plugin-checks-and-tests.md`:
  CLI, debugging order, checks and tests.
- `references/hosted-ui-authoring.md`, `references/hosted-ui-api.md`: Hosted
  TSX and Markdown surfaces.

Do not answer from memory when a reference covers the question.
