# Plugin Creation Workflow

Only for a plugin that does not exist yet. If the folder already has a
`plugin.toml` (a generated project always does), skip this page: the plugin
exists, and `init` must not run again.

## Minimum Question Set

Ask these unless the conversation already answered them, in user-facing
language. Infer the architecture from the answers; do not ask the user to
design entries.

1. What should the plugin be called?
2. What is it for?
3. Which package type fits?
   - Plugin (default): tools, background work, timers, UI, ordinary external
     services or devices.
   - Adapter: bridges an external protocol or request stream into N.E.K.O
     plugin calls.
4. What should the first version include? (callable entries, background or
   lifecycle work, timers, message reactions, UI, storage/settings,
   cross-plugin calls, external services or devices, a protocol gateway)
5. What is out of scope for the first version?

Derive `plugin_id` from the name (`core-plugin-contract.md`, Identity Lock).

## Follow-ups

Ask guidance follow-ups only when the intent is vague. Ask risk follow-ups
when the answers imply: external protocols or services, credentials or
secrets, persistence or state ownership, UI permissions, background work,
auto-start, timers or shutdown behaviour, adapter behaviour, third-party
dependencies (`dependencies-and-market.md`), or anything that needs a host
change (`execution-boundary.md`).

## Design Brief

Present a short brief before implementation. After confirmation and
scaffolding, save it as `<plugin_dir>/DESIGN.md`:

```md
# <Plugin Name> Design Brief

## Identity Lock
- plugin_id:
- name:
- entry:
- main class:

## Purpose

## Package Type and Capabilities

## First Version Scope

## Out of Scope

## Technical Needs
- plugin.toml sections:
- SDK surfaces:
- UI surfaces:
- state/config:
- lifecycle/background work:
- external integrations and dependencies:

## Risk Follow-ups
```

Keep it short; it is not a changelog or API reference.

## Scaffold

Always scaffold with the CLI, never by hand: it writes the manifest, entry
class, tests, Git repository, Market workflows, and agent guides that the
checks expect.

- Source checkout: `uv run neko-plugin init <plugin_id> --type plugin --name
  "<Plugin Name>"` (`--type adapter` for adapters). The folder is the
  plugin's own Git repository and its only editable source: no second copy,
  symlink, or imported package.
- Desktop build: the Plugin studio (插件制作工作台) creates the project.

Then:

1. Read the generated `plugin.toml`, entry class, and `AGENTS.md`.
2. Check the Identity Lock against the generated files.
3. Write `DESIGN.md`.
4. Add i18n (`zh-CN` and `en`, `core-plugin-contract.md`).
   If the plugin needs a panel, start from a built-in Hosted TSX panel:
   [lifekit](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/plugins/lifekit/ui/panel.tsx),
   [mcp_adapter](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/plugins/mcp_adapter/ui/panel.tsx),
   [netease_music](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/plugins/netease_music/ui/panel.tsx)
   (`hosted-ui-authoring.md`).
5. Implement inside `<plugin_dir>`, then validate in the order of
   `plugin-cli-and-debugging.md`.

## Implementation Gate

Do not implement until these are clear: Identity Lock, package type and
capabilities, first-version scope, out of scope, open risk follow-ups, and
which entries are callable (all `async def`, no work at import time).

## Default Location [checkout only]

Without `--output`, `init` creates the plugin at `plugin/plugins/<plugin_id>/`
inside the checkout.
