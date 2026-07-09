# Hosted UI API

This is the public Hosted TSX API exposed by `@neko/plugin-ui`. Source of truth: `plugin/sdk/hosted-ui/index.d.ts` and `frontend/plugin-manager/src/components/plugin/hosted/ui-kit/runtime.js`.

## Types

```ts
type Tone = "primary" | "success" | "warning" | "danger" | "info" | "default"
```

Use `Tone` for buttons, badges, alerts, dialogs, and action controls.

```ts
type JsonSchema = {
  type?: string
  title?: string
  description?: string
  default?: any
  enum?: any[]
  properties?: Record<string, JsonSchema>
  items?: JsonSchema
  required?: string[]
}
```

Used by plugin entries/actions and config/state schemas.

```ts
type HostedAction = {
  id: string
  entry_id?: string
  label?: string
  description?: string
  input_schema?: JsonSchema
  icon?: string | null
  tone?: Tone
  group?: string | null
  order?: number
  confirm?: boolean | string
  refresh_context?: boolean
}
```

Action metadata produced by Python `@ui.action` plus `@plugin_entry`.

```ts
type HostedApi = {
  call: (actionId: string, args?: Record<string, any>, options?: { timeoutMs?: number }) => Promise<any>
  refresh: () => Promise<any>
}
```

Bridge to the Plugin Manager host. `call` invokes an authorized UI action; `refresh` reloads context and re-renders the surface.

```ts
type PluginSurfaceProps<State = Record<string, any>> = {
  plugin: Record<string, any>
  surface: Record<string, any>
  state: State
  stateSchema?: JsonSchema | null
  actions: HostedAction[]
  entries: Array<Record<string, any>>
  config?: { schema: JsonSchema; value: Record<string, any>; readonly?: boolean }
  warnings: Array<{ path: string; code: string; message: string }>
  locale: string
  t: (source: string, params?: Record<string, any>) => string
  i18n: { locale: string; default_locale?: string; messages?: Record<string, Record<string, string>> }
  api: HostedApi
  useLocalState: <T>(key: string, initialValue: T | (() => T)) => [T, (next: T | ((previous: T) => T)) => T]
}
```

Default export components receive this object. `config` is present only when the surface has permission to read plugin config; always handle it as optional.

## Layout Components

- `Page({ title?, subtitle?, children?, className? })`: page wrapper with optional header.
- `Card({ title?, children?, className? })`: section card with optional title and body.
- `Section({ children?, className? })`: unframed section wrapper.
- `Heading({ as?, children?, className? })`: heading element, defaults to `h2`.
- `Stack({ gap?, children?, className? })`: vertical stack. `gap` is pixels.
- `Grid({ cols?, gap?, children?, className? })`: grid layout. `cols` defaults to 2.
- `Divider()`: horizontal divider.
- `Toolbar({ children?, className? })`: toolbar container.
- `ToolbarGroup({ children?, className? })`: grouped toolbar controls.

## Text And Status

- `Text({ children?, className? })`: paragraph text.
- `StatusBadge({ tone?, status?, label?, children?, className? })`: small badge. Tone uses `tone` or `status`.
- `StatCard({ label?, value?, className? })`: metric card.
- `KeyValue({ data?, items?, children?, className? })`: key/value rows. `items` entries use `{ key?, label?, value? }`.
- `Alert({ tone?, message?, children?, className? })`: inline alert.
- `InlineError({ title?, message?, error?, details?, children?, className? })`: formatted error block.
- `EmptyState({ title?, description?, children?, className? })`: empty placeholder.
- `Progress({ label?, value?, className? })`: percentage bar clamped to 0-100.
- `JsonView({ data?, value?, className? })`: JSON pretty printer using `CodeBlock`.

## Data Display

```ts
type DataTableColumn<T> =
  | string
  | { key: keyof T | string; label?: any; render?: (row: T, index: number) => any }
```

`DataTable<T>({ data?, columns?, rowKey?, selectedKey?, emptyText?, maxRows?, onSelect?, className? })`

- `data` defaults to `[]`.
- `columns` defaults to object keys from the first row.
- `rowKey` identifies rows for selection styling.
- `selectedKey` applies selected styling when it matches the row key.
- `maxRows` truncates displayed rows.
- `onSelect(row, index)` fires when a row is clicked.
- Boolean cells render as `StatusBadge`.
- `render(row, index)` customizes a cell and is wrapped with runtime error reporting.

`List<T>({ items?, render?, children?, className? })`

- With `children`, renders children directly.
- Otherwise maps `items`; `render(item, index)` customizes each row.

## Form Controls

- `Field({ label?, help?, error?, required?, children?, className? })`: label/help/error wrapper.
- `Input({ value?, placeholder?, invalid?, error?, onChange?, className? })`: text input. `onChange(value: string)`.
- `Textarea({ value?, placeholder?, invalid?, error?, onChange?, className? })`: multiline input. `onChange(value: string)`.
- `Select({ value?, options?, invalid?, error?, onChange?, className? })`: select input. `options` are strings or `{ value, label? }`.
- `Switch({ checked?, label?, invalid?, error?, onChange?, children?, className? })`: checkbox-style boolean control.
- `Form({ onSubmit?, children?, className? })`: prevents default submit and invokes `onSubmit(event)`.

Input and Textarea preserve composition state for IME text entry.

## Buttons And Actions

- `Button({ tone?, variant?, type?, disabled?, onClick?, children?, className? })`: basic button. `variant` aliases `tone`.
- `ButtonGroup({ children?, className? })`: grouped buttons.
- `ActionButton({ action?, actionId?, label?, tone?, values?, args?, refresh?, confirm?, onResult?, onError?, children?, className? })`: calls a Hosted UI action.
  - `action` is a `HostedAction`; otherwise pass `actionId`.
  - Sends `values` or `args`.
  - Confirms with `window.confirm` when `confirm` or action `confirm` is set.
  - Refreshes context unless action `refresh_context === false` or prop `refresh === false`.
- `RefreshButton({ label?, tone?, onRefresh?, onError?, children?, className? })`: calls `api.refresh()`.
- `ActionForm({ action?, submitLabel?, successMessage?, onResult?, onError?, children?, className? })`: builds a form from `action.input_schema`.
  - Supports enum -> `Select`, boolean -> `Switch`, object/array -> `Textarea`, everything else -> `Input`.
  - Performs shallow required/type/enum validation before call.
  - Calls `api.call(action.entry_id || action.id, values)`.

## Dialogs, Errors, And Feedback

- `Modal({ open?, title?, footer?, closeOnBackdrop?, onClose?, children?, className? })`: modal dialog. Escape calls `onClose`.
- `ConfirmDialog({ open?, title?, message?, tone?, confirmLabel?, cancelLabel?, closeOnBackdrop?, onConfirm?, onCancel?, children?, className? })`: modal confirm wrapper.
- `ErrorBoundary({ fallback?, title?, children? })`: catches render errors in child components.
  - `fallback` can be a node or `(error, reset) => node`.
- `showToast(message, options?)`: shows a toast and returns a cleanup function. `options` can be `{ tone?, timeout? }` or a `Tone`.
- `useToast()`: returns `{ show, info, success, warning, error }`.
- `useConfirm()`: returns an async confirm function accepting a string or `{ title?, message?, tone?, confirmLabel?, cancelLabel? }`.
- `AsyncBlock<T>({ load, deps?, fallback?, loadingText?, error?, errorTitle?, children?, className? })`: runs `load()` with `useAsync` semantics and renders loading/error/data states.
  - `fallback` renders while loading.
  - `error` can be a node or `(error, reload) => node`.
  - Use this for small UI-local async reads; prefer Python `@ui.context` for primary plugin state.

## Documentation Helpers

- `CodeBlock({ children?, className? })`: preformatted block.
- `Tip({ children?, className? })`: informational callout.
- `Warning({ children?, className? })`: warning callout.
- `Steps({ children?, className? })`: step list wrapper.
- `Step({ index?, title?, children?, className? })`: individual step.
- `Tabs({ id?, activeId?, items?, onChange?, children?, className? })`: tabs.
  - `items` entries use `{ id?, label?, title?, content? }`.
  - Active tab is persisted with `useLocalState("tabs:<id>")`.
  - If `children` is present, children are used as the panel content.

## Hooks

These hooks are implemented by the hosted runtime, not React.

- `useState<T>(initialValue)`: local component state.
- `useReducer<S, A>(reducer, initialArg, init?)`: reducer state.
- `useEffect(effect, deps?)`: effect after render; cleanup runs on dependency changes/unmount.
- `useLayoutEffect(effect, deps?)`: currently aliases `useEffect`; do not depend on React layout timing.
- `useMemo<T>(factory, deps?)`: memoized value.
- `useCallback<T>(callback, deps?)`: memoized function.
- `useRef<T>(initialValue)`: stable `{ current }` object.
- `useLocalState<T>(key, initialValue)`: iframe-local state keyed by string and retained across context refreshes while the iframe lives.
- `useDebounce<T>(value, delay?)`: debounced value.
- `useDebouncedState<T>(initialValue, delay?)`: `[value, setValue, debouncedValue]`.
- `useForm<T>(initialValues)`: returns `{ values, setValues, setField, field, checkbox, reset }`.
- `useAsync<T>(loader, deps?)`: returns `{ loading, error, data, reload }`.
- `useI18n()`: returns `{ t, locale }`.

`useForm` helpers:

- `setField(name, value)` updates one field.
- `field(name)` returns `{ value, onChange }` for `Input`, `Select`, or `Textarea`.
- `checkbox(name)` returns `{ checked, onChange }` for `Switch`.
- `reset(next?)` resets to initial values or the supplied values.

## Low-Level Runtime

- `h(type, props, ...children)`: create a virtual node.
- `Fragment`: fragment marker.
- `render(vnode, container)`: render into a DOM element.

Most plugin TSX files should not call these directly; JSX compiles to `h(...)` automatically.

## Import Pattern

```tsx
import { Page, Card, Button, useState } from "@neko/plugin-ui"
import type { PluginSurfaceProps, HostedAction } from "@neko/plugin-ui"
```

Avoid importing from runtime implementation files. The virtual module `@neko/plugin-ui` is the stable authoring surface.
