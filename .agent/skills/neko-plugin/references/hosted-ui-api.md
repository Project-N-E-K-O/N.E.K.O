# Hosted UI API

The public Hosted TSX API of the virtual module `@neko/plugin-ui`. Source of
truth: [index.d.ts](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/sdk/hosted-ui/index.d.ts)
(behaviour: [runtime.js](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/frontend/plugin-manager/src/components/plugin/hosted/ui-kit/runtime.js)).
If a name is not listed in `index.d.ts`, it does not exist: do not guess
props from React or other UI kits.

```tsx
import { Page, Card, Button, useState } from "@neko/plugin-ui"
import type { PluginSurfaceProps, HostedAction } from "@neko/plugin-ui"
```

Every component accepts `CommonProps`: `{ key?, className?, children? }`.

## Surface Props

```ts
type PluginSurfaceProps<State = Record<string, any>> = {
  plugin: Record<string, any>
  host?: { origin?: string }
  surface: Record<string, any>
  state: State                      // returned by @ui.context
  stateSchema?: JsonSchema | null
  actions: HostedAction[]           // entries exposed by @ui.action
  entries: Array<Record<string, any>>
  config: { schema: JsonSchema; value: Record<string, any>; readonly?: boolean }
  warnings: Array<{ path: string; code: string; message: string }>
  locale: string
  t: (source: string, params?: Record<string, any>) => string
  i18n: HostedI18n                  // { locale, default_locale?, messages? }
  api: HostedApi
  useLocalState: <T>(key: string, initialValue: T | (() => T)) => [T, LocalStateSetter<T>]
}
```

The default export receives this object. Without `config:read`, `config` is
an empty read-only placeholder; without `action:call`, `actions` is empty.

## Host Bridge: `HostedApi`

```ts
type HostedApi = {
  call: (actionId: string, args?: Record<string, any>,
         options?: { timeoutMs?: number; signal?: AbortSignal; userInitiated?: boolean }) => Promise<any>
  parseDocument: (file: File, options?: { timeoutMs?: number; signal?: AbortSignal }) => Promise<ParsedHostedDocument>
  refresh: () => Promise<any>
  display: PluginDisplayApi
}
```

- `call`: runs a `@ui.action` entry. Needs `action:call`, a running plugin.
  It rejects on failure: 409 when the plugin is not running, 403 when the
  entry is not a UI action or `action:call` is missing, 404 when the surface
  or entry is unknown, 500 when the entry failed. The wait is the shorter of
  `timeoutMs` (default 30 s) and the entry's own timeout. After an `await`
  the click no longer counts as a user action; pass `userInitiated: true` for
  a call the user started.
- `parseDocument`: text and metadata from a user-selected PDF/DOCX
  (`ParsedHostedDocument`: `name`, `sourceType`, `mime`, `originalSize`,
  `chars`, `encoding`, `truncated`, `content`, `meta?`). Needs the
  `document:parse` permission and must be called directly from a user event
  handler (change, click, drop) or right after a `useConfirm()` the user
  accepted; `userInitiated` does not apply here, later calls are rejected.
- `refresh`: reloads `@ui.context` state and re-renders.
- `display`: `getState()`, `requestMode("focus" | "fullscreen")`, `exit()`,
  `onStateChange(listener)` (returns an unsubscribe function). Requests
  resolve to `{ status, state, reason? }` with `status` one of `applied`,
  `declined`, `unsupported`, `cancelled`, `busy`, `failed`; the host asks the
  user to confirm.

## Shared Types

```ts
type Tone = "primary" | "success" | "warning" | "danger" | "info" | "default"
type JsonSchema = { type?; title?; description?; default?; enum?; properties?; items?; required? }
type HostedAction = { id: string; entry_id?; label?; description?; input_schema?: JsonSchema;
                      icon?; tone?: Tone; group?; order?; confirm?: boolean | string; refresh_context?: boolean }
type UiOption = string | { value: any; label?: any; title?: any; disabled?: boolean }
type DataTableColumn<T> = string | { key: keyof T | string; label?: any; render?: (row: T, index: number) => any }
```

Also exported: `HostedI18n`, `RefObject`, `StateSetter`, `LocalStateSetter`,
`ElementSize`, `ClipboardState`, `AsyncState`, `FormState`, `FormErrors`,
`FormTouched`, `ToastOptions`, `ToastApi`, `ToastPromiseMessages`,
`UploadedFileInfo`, `ArtifactType`, `ArtifactView`, `ArtifactLike`,
`NormalizedArtifact`, `PluginDisplay*`, `ParsedHostedDocument`,
`CommonProps`. Import them with `import type`.

## Layout

- `Page({ title?, subtitle? })`, `Card({ title? })`, `Section`, `Heading({ as? })` (default `h2`), `Divider()`
- `Container({ maxWidth?, width?, padding? })`
- `Stack({ gap? })` (vertical), `Inline({ gap?, align?, justify?, wrap? })` (horizontal)
- `Grid({ cols?, gap? })` (`cols` defaults to 2), `Columns({ cols? | columns?, minWidth? | minColumnWidth?, gap?, fluid? })`
- `Split({ ratio?, template?, direction?, gap?, align? })`
- `ScrollArea({ height?, maxHeight?, minHeight?, padding?, axis?, autoScroll?, deps?, scrollBehavior? })`
- `Toolbar`, `ToolbarGroup`, `Accordion({ id?, title? | label?, open? })`, `Tooltip({ content? | label? | title?, placement? })`
- `Tabs({ id?, activeId?, items?, onChange? })`: `items` are `{ id?, label?, title?, content? }`; the active tab persists via `useLocalState("tabs:<id>")`; with `children`, children are the panel content.

## Text and Status

- `Text`, `TextBlock({ text? | value? })`, `CodeBlock`, `Tip`, `Warning`, `Steps`, `Step({ index?, title? })`
- `Markdown({ source? | text? })`: styled preformatted text; it does **not** parse Markdown into HTML.
- `StatusBadge({ tone?, status?, label? })`, `StatCard({ label?, value? })`
- `KeyValue({ data?, items? })` (`items`: `{ key?, label?, value? }`)
- `Progress({ label?, value?, indeterminate? })` (value clamped to 0-100)
- `Alert({ tone?, message? })`, `InlineError({ title?, message?, error?, details? })`, `EmptyState({ title?, description? })`
- `JsonView({ data? | value? })`, `LogViewer({ text? | value?, autoScroll?, deps?, scrollBehavior? })`, `JsonEditorLite({ value? | data?, mode?, onChange? })`

## Data

- `DataTable<T>({ data?, columns?, rowKey?, selectedKey?, emptyText?, maxRows?, onSelect? })`: columns default to the first row's keys; boolean cells render as `StatusBadge`; `render(row, index)` customizes a cell; `onSelect(row, index)` on click.
- `List<T>({ items?, render? })`: with `children`, renders them directly.
- `Gallery<T>({ items?, columns? | cols?, emptyText?, onSelect? })`

## Media and Artifacts

- Upload: `ImageUpload`, `AudioUpload`, `VideoUpload` (`{ value?, label?, placeholder?, accept?, maxBytes?, compact?, variant?, onChange?(artifact), onError? }`; `ImageUpload` also `alt?`). They hand you an `ArtifactLike`; send it to Python with an action.
- Show: `ImagePreview`, `AudioPlayer`, `VideoPlayer` (`{ artifact? | src? | value?, label?, caption?, emptyText? }` plus `alt?` / `controls?`, `preload?`, `poster?`)
- `FileDownload({ href? | url? | dataUrl? | path?, filename?, label?, copiedLabel?, tone?, target?, openExternal? })`
- `ArtifactRenderer({ artifact? | item? | value?, render? })`, `ArtifactCard({ ..., label?, renderArtifact? })`, `ArtifactList({ items?, layout?, empty?, emptyText?, cardClassName?, renderArtifact? })`
- Helpers: `normalizeArtifact(value)`, `detectArtifactType(value)` (`image`, `audio`, `video`, `text`, `json`, `file`, `unknown`)

## Forms and Controls

- `Form({ onSubmit? })` (prevents the default submit), `FormSection({ title?, description? })`, `FormActions({ align? })`
- `Field({ label?, help?, error?, required? })`
- `Input({ type?, value?, placeholder?, min?, max?, step?, invalid?, error?, disabled?, onChange?(string) })`, `PasswordInput`, `Textarea` (same shape, string values)
- `NumberInput({ value?, min?, max?, step?, onChange?(number | string), ... })`, `Slider({ value?, min?, max?, step?, showValue?, onChange?(number) })`
- `Select({ value?, options?: UiOption[], onChange? })`, `RadioGroup({ name?, value?, options?, onChange? })`, `SegmentedControl({ value?, options?, onChange? })`
- `Switch({ checked?, label?, onChange?(boolean) })`, `Checkbox({ checked? | value?, label?, onChange?(boolean) })`, `CheckboxGroup({ value?: any[], options?, onChange?(any[]) })`

Inputs keep IME composition state. All controls take `disabled?`; text
controls also `invalid?` and `error?`.

## Buttons and Actions

- `Button({ tone?, variant?, type?, disabled?, onClick? })` (`variant` aliases `tone`), `ButtonGroup`
- `ActionButton({ action? | actionId?, label?, tone?, values? | args?, refresh?, confirm?, onResult?, onError? })`: calls an action; when `confirm` or the action's `confirm` is set it asks via `window.confirm`, which the sandbox blocks, so the click silently does nothing (ask with `useConfirm()` instead); refreshes context unless `refresh === false` or the action has `refresh_context: false`.
- `ActionForm({ action?, submitLabel?, successMessage?, onResult?, onError? })`: a form built from `action.input_schema` (enum → `Select`, boolean → `Switch`, object/array → `Textarea`, else `Input`) with shallow required/type/enum checks.
- `RefreshButton({ label?, tone?, onRefresh?, onError? })`: calls `api.refresh()`.
- `AsyncBlock<T>({ load, deps?, fallback?, loadingText?, error?, errorTitle? })`: small UI-local async reads; primary state belongs in `@ui.context`.

## Dialogs and Feedback

- `Modal({ open?, title?, footer?, closeOnBackdrop?, closeOnEscape?, lockScroll?, size?, onClose? })`
- `ConfirmDialog({ open?, title?, message?, tone?, confirmLabel?, cancelLabel?, closeOnBackdrop?, onConfirm?, onCancel? })`
- `ErrorBoundary({ fallback?, title? })`: `fallback` is a node or `(error, reset) => node`.
- `showToast(message, options? | tone)` returns a dismiss function; `showToast.promise(promise, messages?, options?)`.
- `useToast()`: `{ show, info, success, warning, error, promise }`.
- `useConfirm()`: `await confirm("text" | { title?, message?, tone?, confirmLabel?, cancelLabel? })` → `boolean`.

## Hooks

Implemented by the hosted runtime, not React.

- `useState`, `useReducer`, `useEffect`, `useMemo`, `useCallback`, `useRef`
- `useLayoutEffect`: same timing as `useEffect`; do not rely on pre-paint.
- `useLocalState(key, initial)`: state kept across context refreshes while the iframe lives.
- `useDebounce(value, delay?)`, `useDebouncedState(initial, delay?)` → `[value, setValue, debounced]`
- `useAsync(loader, deps?)` → `{ loading, error, data, reload }`
- `useForm(initialValues, { validate? }?)` → `values`, `setValues`, `setField`, `field(name)` (`{ value, onChange, onBlur, error, touched }`), `checkbox(name)`, `reset`, `touched`, `errors`, `setError`, `clearError`, `dirty`, `submitCount`, `validate`, `handleSubmit(onValid, onInvalid?)`
- `useElementSize(ref)` → `{ width, height }`; `useScrollIntoView(ref, defaults?)`; `useScrollToBottom(ref, deps?, { enabled?, behavior? }?)`
- `useClipboard()` → `{ write, read, copied, error }`
- `useI18n()` → `{ t, locale }`

## Low-Level Runtime

`h(type, props, ...children)`, `Fragment`, `render(vnode, container)`. JSX
compiles to `h(...)`; plugin code rarely calls these.
