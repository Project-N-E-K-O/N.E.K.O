# Hosted UI Authoring

Use this when adding or reviewing a N.E.K.O plugin UI surface.

Primary source files:

- `plugin/core/ui_manifest.py`: manifest normalization, mode inference, permissions, default context.
- `plugin/server/application/plugins/ui_query_service.py`: surface lookup, context/action authorization, dependency loading.
- `plugin/sdk/plugin/ui.py`: Python `@ui.context` and `@ui.action` decorators.
- `frontend/plugin-manager/src/components/plugin/HostedSurfaceFrame.vue`: iframe loading, markdown rendering, Hosted TSX bridge.
- `frontend/plugin-manager/src/components/plugin/hosted/tsxRuntime.ts`: Hosted TSX document build and sandbox runtime.
- `frontend/plugin-manager/src/components/plugin/hosted/hostedTsxModule.mjs`: import/export contract and dependency bundling.
- `frontend/plugin-manager/src/components/plugin/hosted/ui-kit/runtime.js`: public UI kit runtime implementation.
- `plugin/sdk/hosted-ui/index.d.ts`: public TSX API types.

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
- Valid permissions are `state:read`, `config:read`, `config:write`, `action:call`, `logs:read`, and `runs:read`.
- Add only the permissions the surface needs. `action:call` is required for Hosted TSX action calls.
- Entry paths are resolved inside `plugin/plugins/<plugin_id>/`; path traversal is rejected.
- The entry file must exist or the surface is marked unavailable.
- Locale-specific surface files can be placed next to the default file as `<stem>.<locale>.<ext>`; the default unsuffixed file is treated as the base source.

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
- Do not import npm packages or external bare modules.
- Same-plugin relative imports are supported for `.tsx`, `.ts`, `.jsx`, and `.js` files.
- Relative dependencies must stay inside the plugin root. Runtime dependency discovery limits are 32 files and 512 KiB total.
- Dynamic `import()` is rejected.
- The linker supports simple named exports in relative helper modules. Avoid re-exports, `export *`, export lists, enums, generators, abstract classes, destructured exports, and multi-declarator exports.
- The iframe sandbox for Hosted TSX/Markdown is `allow-scripts`; do not rely on normal page globals outside the hosted bridge.
- The runtime uses a lightweight React-like renderer. Use the provided hooks; do not assume React is installed.
- `useLayoutEffect` currently uses the same timing as `useEffect`; do not depend on React pre-paint semantics.

## Props And Bridge

`PluginSurfaceProps` includes:

- `plugin`: plugin metadata.
- `surface`: normalized surface metadata.
- `state`: object returned by `@ui.context`.
- `stateSchema`: optional context schema.
- `actions`: UI actions exposed by `@ui.action`.
- `entries`: entry metadata.
- `config`: config schema/value snapshot, present when permitted.
- `warnings`: surface warnings.
- `locale`, `i18n`, and `t(...)`.
- `api.call(actionId, args, options)` and `api.refresh()`.
- `useLocalState(key, initialValue)`.

Prefer `ActionButton` or `ActionForm` over raw `api.call` unless the UI needs custom control flow.

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

For a plugin with Hosted TSX:

```bash
cd frontend/plugin-manager
npm run check-hosted-tsx -- plugin/plugins/<plugin_id>
```

Use broader checks when touching runtime behavior, action bridge, i18n, or UI kit:

```bash
cd frontend/plugin-manager
npm run test:hosted-script
npm run test:hosted
npm run test:hosted:e2e
```

`uv run neko-plugin check <plugin_id|plugin_path>` verifies manifest shape and that UI entry files exist, but it only performs shallow `input_schema` checks for literal dicts and does not fully type-check Hosted TSX or exercise iframe behavior.
