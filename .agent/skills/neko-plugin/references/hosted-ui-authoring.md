# Hosted UI Authoring

Use this when adding or reviewing a N.E.K.O plugin UI surface.

Sources of truth, when this page is not enough:
[ui_manifest.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/core/ui_manifest.py) (manifest, modes, permissions),
[ui.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/sdk/plugin/ui.py) (`@ui.context`, `@ui.action`),
[hostedTsxModule.mjs](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/frontend/plugin-manager/src/components/plugin/hosted/hostedTsxModule.mjs)
(import/export rules), and the [Hosted UI guide](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/docs/plugins/hosted-ui.md).
Component API: `hosted-ui-api.md`.

## Mode Choice

Use the smallest surface that satisfies the UI need:

- `hosted-tsx`: default for new interactive UI: settings panels, dashboards, tables, forms, action buttons, local state, i18n-aware views.
- `markdown`: read-only guides or docs. It renders basic headings, paragraphs, lists, code blocks, links, and blockquotes. Do not use it for actions or scripts.
- `static`: legacy standalone HTML in an iframe. Use only for existing custom HTML/CSS/JS pages or browser/runtime behavior Hosted TSX cannot support.

Surface kind is placement; mode is rendering:

- `[[plugin.ui.panel]]`: plugin management/dashboard surface.
- `[[plugin.ui.guide]]`: quickstart or guide surface.
- `[[plugin.ui.docs]]`: documentation surface.

## plugin.toml

Declare Hosted UI under `[plugin.ui]`. If `mode` is omitted, it is inferred from `entry`: `.tsx`/`.jsx` -> `hosted-tsx`, `.md`/`.mdx` -> `markdown`, `.html`/`.htm` -> `static`.

```toml
[plugin]
id = "my_plugin"
name = "My Plugin"
entry = "plugin.plugins.my_plugin:MyPlugin"

[plugin.i18n]
default_locale = "en"
locales_dir = "i18n"

[plugin.ui]
enabled = true

[[plugin.ui.panel]]
id = "main"
title = "My Plugin"
entry = "ui/panel.tsx"
context = "dashboard"
permissions = ["state:read", "config:read", "action:call"]

[[plugin.ui.guide]]
id = "quickstart"
title = "Quickstart"
entry = "docs/quickstart.md"
permissions = ["state:read"]
```

Manifest rules:

- `id` defaults from the entry stem; use explicit ids for stable links.
- `context` defaults to the surface id for `panel` and `guide`; set it explicitly when the Python context id differs.
- `permissions` defaults to `["state:read", "config:read", "action:call"]` for panels and `["state:read"]` for guide/docs.
- Valid permissions are `state:read`, `config:read`, `config:write`, `action:call`, `document:parse`, `logs:read`, and `runs:read`. `neko-plugin check` currently warns that `document:parse` is unrecognized ([#3809](https://github.com/Project-N-E-K-O/N.E.K.O/issues/3809)); that warning is wrong and can be ignored.
- Add only the permissions the surface needs. `action:call` is required for Hosted TSX action calls.
- Entry paths are resolved inside `<plugin_dir>` (the folder holding `plugin.toml`); path traversal is rejected.
- The entry file must exist or the surface is marked unavailable.
- For `hosted-tsx` and `markdown` surfaces, locale-specific files sit next to the default file as `<stem>.<locale>.<ext>` (`static` surfaces always serve their one entry file). The unsuffixed file is the zh-CN version; ship at least `<stem>.en.<ext>` too. Other locales try their exact and primary tag, then (for other `zh-*` variants such as `zh-TW`) `zh-CN`, then `en`, then the unsuffixed file.

## Python Context And Actions

Use the public SDK facade:

```python
from plugin.sdk.plugin import NekoPluginBase, Ok, neko_plugin, plugin_entry, tr, ui


@neko_plugin
class MyPlugin(NekoPluginBase):
    @ui.context(id="dashboard")
    async def dashboard(self):
        return {
            "items": [{"id": "demo", "status": "ready"}],
        }

    @ui.action(
        label=tr("actions.refresh.label", default="Refresh"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="refresh_item",
        name=tr("entries.refresh.name", default="Refresh Item"),
        description=tr("entries.refresh.description", default="Refresh an item."),
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": tr("fields.itemId", default="Item ID")},
            },
            "required": ["item_id"],
        },
    )
    async def refresh_item(self, item_id: str, **_):
        return Ok({"message": f"Refreshed {item_id}"})
```

Rules:

- `@ui.context(id=...)` provides `props.state` for surfaces whose `context` matches that id.
- `@ui.action(...)` exposes an existing `@plugin_entry` to Hosted TSX. A surface cannot call arbitrary entries; it can call only exposed UI actions.
- Hosted action calls require the plugin to be running and the surface to include `action:call`.
- `refresh_context=True` lets `ActionButton` and `ActionForm` refresh `props.state` after success.
- Use `tr(...)` for user-visible action labels, entry names, descriptions, and schema labels when the plugin has i18n.

## Hosted TSX File

Hosted TSX must export a default function component. Keep business logic in Python and use TSX for presentation, local UI state, filtering, forms, and calls to exposed actions.

```tsx
import {
  ActionButton,
  Card,
  DataTable,
  Page,
  Stack,
  Text,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type Item = { id: string; status: string }
type State = { items?: Item[] }

export default function Panel(props: PluginSurfaceProps<State>) {
  const { actions, state, t } = props
  const refresh = actions.find((action) => action.id === "refresh_item") as HostedAction | undefined

  return (
    <Page title={props.plugin.name} subtitle={t("panel.subtitle", { defaultValue: "Manage items." })}>
      <Card title={t("panel.items", { defaultValue: "Items" })}>
        <Stack>
          <DataTable
            data={state.items || []}
            rowKey="id"
            columns={[
              { key: "id", label: t("fields.itemId", { defaultValue: "Item ID" }) },
              { key: "status", label: t("fields.status", { defaultValue: "Status" }) },
            ]}
          />
          {refresh ? (
            <ActionButton action={refresh} values={{ item_id: "demo" }} />
          ) : (
            <Text>{t("panel.noActions", { defaultValue: "No actions exposed." })}</Text>
          )}
        </Stack>
      </Card>
    </Page>
  )
}
```

TSX runtime rules:

- Import components, hooks, and types from `@neko/plugin-ui`.
- Do not import npm packages or external bare modules; only `@neko/plugin-ui` (or `neko:ui`) and relative files.
- Same-plugin relative imports are supported for `.tsx`, `.ts`, `.jsx`, and `.js` files.
- Relative dependencies must stay inside the plugin root. Runtime dependency discovery limits are 32 files and 512 KiB total.
- Dynamic `import()` is rejected.
- The linker supports simple named exports (`export function`, `export const`) in relative helper modules. These are rejected outright, not merely discouraged: `export let`/`export var`, namespaces, re-exports, `export *`, export lists, enums, generators, abstract classes, destructured exports, and multi-declarator exports. An installed plugin is never run through `check-hosted-tsx`, so the rejection shows up as a 400 error when the panel is opened.
- Write `props.api.call(...)` in full. The checker rejects any bare `api.` (even `const { api } = props; api.call(...)`, or `api.` in a comment).
- The runtime uses a lightweight React-like renderer. Use the provided hooks; do not assume React is installed.
- `useLayoutEffect` currently uses the same timing as `useEffect`; do not depend on React pre-paint semantics.

## What The Sandbox Does Not Have

Hosted TSX and Markdown run in an `allow-scripts` iframe with an opaque origin:

- no `localStorage`/`sessionStorage` (they throw): use `useLocalState` for UI state and Python for anything durable;
- no `alert`/`confirm`/`prompt` or popups: `window.confirm` returns `false` without showing anything, so a `confirm` on `ActionButton`/`ActionForm` (set directly or through `@ui.action(confirm=...)`) silently cancels the action. `confirm` is not a safety gate: a direct `props.api.call(...)` ignores it; use `useConfirm()` and then `props.api.call(...)`, followed by `props.api.refresh()` if the action changes `@ui.context` state (only `ActionButton`/`ActionForm` refresh on their own);
- no reliable `fetch` to N.E.K.O (cross-origin from `null`) and no host page globals: everything goes through `props.api`;
- plugin logic and secrets stay in Python; the panel only renders state and calls actions.

## Props And Bridge

The default export receives `PluginSurfaceProps` (`state`, `actions`, `config`, `t`, `api`, ...); the full shape and `api.call` / `api.refresh` / `api.parseDocument` / `api.display` are in `hosted-ui-api.md`. Prefer `ActionButton` or `ActionForm` over raw `api.call` unless the UI needs custom control flow.

## Markdown Surfaces

Markdown mode uses a built-in renderer, not arbitrary HTML execution. It supports:

- headings `#`, `##`, `###`
- paragraphs
- bullet lists
- blockquotes
- fenced code blocks
- inline code
- http/https links opened through the host

Use Markdown only for read-only quickstarts, docs, and reference text.

## Validation

`neko-plugin check` verifies the manifest and that UI entry files exist (it currently warns about `document:parse` by mistake). It does not type-check Hosted TSX, render Markdown, or exercise the iframe. Without a checkout, ask the user to open the surface after Start/Reload and report the warnings it shows.

### Hosted TSX checks [checkout only]

These need a N.E.K.O source checkout with the frontend dependencies installed, and the plugin must sit inside that checkout: the script refuses paths outside the repository root.

```bash
cd frontend/plugin-manager
npm run check-hosted-tsx -- plugin/plugins/<plugin_id>
```

Broader runtime checks (only when touching hosted runtime behaviour): `npm run test:hosted-script`, `npm run test:hosted`, `npm run test:hosted:e2e` in the same folder.
