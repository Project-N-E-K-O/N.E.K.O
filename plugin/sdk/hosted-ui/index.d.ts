export type Tone = "primary" | "success" | "warning" | "danger" | "info" | "default"

export type JsonSchema = {
  type?: string
  title?: string
  description?: string
  default?: any
  enum?: any[]
  properties?: Record<string, JsonSchema>
  items?: JsonSchema
  required?: string[]
}

export type HostedAction = {
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

export type HostedApi = {
  call: (actionId: string, args?: Record<string, any>) => Promise<any>
  refresh: () => Promise<any>
}

export type HostedI18n = {
  locale: string
  default_locale?: string
  messages?: Record<string, Record<string, string>>
}

export type LocalStateSetter<T> = (next: T | ((previous: T) => T)) => T
export type StateSetter<T> = (next: T | ((previous: T) => T)) => T
export type RefObject<T> = { current: T }
export type AsyncState<T> = { loading: boolean; error: any; data: T | undefined; reload: () => any }
export type FormState<T extends Record<string, any>> = {
  values: T
  setValues: (next: T | ((previous: T) => T)) => T
  setField: <K extends keyof T>(name: K, value: T[K]) => T
  field: <K extends keyof T>(name: K) => { value: T[K]; onChange: (value: T[K]) => T }
  checkbox: <K extends keyof T>(name: K) => { checked: boolean; onChange: (value: boolean) => T }
  reset: (next?: T | (() => T)) => T
}

export type PluginSurfaceProps<State = Record<string, any>> = {
  plugin: Record<string, any>
  surface: Record<string, any>
  state: State
  stateSchema?: JsonSchema | null
  actions: HostedAction[]
  entries: Array<Record<string, any>>
  config: {
    schema: JsonSchema
    value: Record<string, any>
    readonly?: boolean
  }
  warnings: Array<{ path: string; code: string; message: string }>
  locale: string
  t: (source: string, params?: Record<string, any>) => string
  i18n: HostedI18n
  api: HostedApi
  useLocalState: <T>(key: string, initialValue: T | (() => T)) => [T, LocalStateSetter<T>]
}

export type CommonProps = {
  className?: string
  children?: any
}

export type DataTableColumn<T = Record<string, any>> = string | {
  key: keyof T | string
  label?: any
  render?: (row: T, index: number) => any
}

export function Page(props: CommonProps & { title?: any; subtitle?: any }): any
export function Card(props: CommonProps & { title?: any }): any
export function Section(props: CommonProps): any
export function Heading(props: CommonProps & { as?: string }): any
export function Stack(props: CommonProps & { gap?: number }): any
export function Grid(props: CommonProps & { cols?: number; gap?: number }): any
export function Text(props: CommonProps): any
export function h(type: any, props: any, ...children: any[]): any
export const Fragment: any
export function render(vnode: any, container: Element): void
export function Button(props: CommonProps & { tone?: Tone; variant?: Tone; type?: string; disabled?: boolean; onClick?: () => void | Promise<void> }): any
export function ButtonGroup(props: CommonProps): any
export function StatusBadge(props: CommonProps & { tone?: Tone; status?: Tone | string; label?: any }): any
export function StatCard(props: CommonProps & { label?: any; value?: any }): any
export function KeyValue(props: CommonProps & { data?: Record<string, any>; items?: Array<{ key?: string; label?: any; value?: any }> }): any
export function DataTable<T = Record<string, any>>(props: CommonProps & {
  data?: T[]
  columns?: Array<DataTableColumn<T>>
  rowKey?: keyof T | string
  selectedKey?: any
  emptyText?: any
  maxRows?: number
  onSelect?: (row: T, index: number) => void
}): any
export function Divider(): any
export function Toolbar(props: CommonProps): any
export function ToolbarGroup(props: CommonProps): any
export function Alert(props: CommonProps & { tone?: Tone; message?: any }): any
export function InlineError(props: CommonProps & { title?: any; message?: any; error?: any; details?: any }): any
export function ErrorBoundary(props: CommonProps & { fallback?: any | ((error: Error, reset: () => void) => any); title?: any }): any
export function EmptyState(props: CommonProps & { title?: any; description?: any }): any
export function Modal(props: CommonProps & { open?: boolean; title?: any; footer?: any; closeOnBackdrop?: boolean; onClose?: () => void }): any
export function ConfirmDialog(props: CommonProps & { open?: boolean; title?: any; message?: any; tone?: Tone; confirmLabel?: any; cancelLabel?: any; closeOnBackdrop?: boolean; onConfirm?: () => void; onCancel?: () => void }): any
export function List<T = any>(props: CommonProps & { items?: T[]; render?: (item: T, index: number) => any }): any
export function Progress(props: CommonProps & { label?: any; value?: number }): any
export function JsonView(props: CommonProps & { data?: any; value?: any }): any
export function Field(props: CommonProps & { label?: any; help?: any; error?: any; required?: boolean }): any
export function Input(props: CommonProps & { value?: any; placeholder?: string; invalid?: boolean; error?: any; onChange?: (value: string) => void }): any
export function Select(props: CommonProps & { value?: any; options?: Array<string | { value: any; label?: any }>; invalid?: boolean; error?: any; onChange?: (value: any) => void }): any
export function Textarea(props: CommonProps & { value?: any; placeholder?: string; invalid?: boolean; error?: any; onChange?: (value: string) => void }): any
export function Switch(props: CommonProps & { checked?: boolean; label?: any; invalid?: boolean; error?: any; onChange?: (value: boolean) => void }): any
export function Form(props: CommonProps & { onSubmit?: (event: Event) => void | Promise<void> }): any
export function ActionButton(props: CommonProps & {
  action?: HostedAction
  actionId?: string
  label?: any
  tone?: Tone
  values?: Record<string, any>
  args?: Record<string, any>
  refresh?: boolean
  confirm?: boolean | string
  onResult?: (result: any) => void
  onError?: (error: Error) => void
}): any
export function RefreshButton(props: CommonProps & { label?: any; tone?: Tone; onRefresh?: () => void; onError?: (error: Error) => void }): any
export function ActionForm(props: CommonProps & { action?: HostedAction; submitLabel?: any; successMessage?: any; onResult?: (result: any) => void; onError?: (error: Error) => void }): any
export function AsyncBlock<T = any>(props: CommonProps & { load: () => Promise<T> | T; deps?: any[]; fallback?: any; loadingText?: any; error?: any | ((error: any, reload: () => any) => any); errorTitle?: any }): any
export function CodeBlock(props: CommonProps): any
export function Tip(props: CommonProps): any
export function Warning(props: CommonProps): any
export function Steps(props: CommonProps): any
export function Step(props: CommonProps & { index?: any; title?: any }): any
export function Tabs(props: CommonProps & { id?: string; activeId?: string; items?: Array<{ id?: string; label?: any; title?: any; content?: any }>; onChange?: (id: string, index: number) => void }): any
export function useI18n(): { t: (key: string, params?: Record<string, any>) => string; locale: string }
export function useState<T>(initialValue: T | (() => T)): [T, StateSetter<T>]
export function useReducer<S, A>(reducer: (state: S, action: A) => S, initialArg: S, init?: (value: S) => S): [S, (action: A) => void]
export function useEffect(effect: () => void | (() => void), deps?: any[]): void
export function useLayoutEffect(effect: () => void | (() => void), deps?: any[]): void
export function useMemo<T>(factory: () => T, deps?: any[]): T
export function useCallback<T extends (...args: any[]) => any>(callback: T, deps?: any[]): T
export function useRef<T>(initialValue: T): RefObject<T>
export function useLocalState<T>(key: string, initialValue: T | (() => T)): [T, LocalStateSetter<T>]
export function useDebounce<T>(value: T, delay?: number): T
export function useDebouncedState<T>(initialValue: T, delay?: number): [T, StateSetter<T>, T]
export function useForm<T extends Record<string, any>>(initialValues: T | (() => T)): FormState<T>
export function useAsync<T>(loader: () => Promise<T> | T, deps?: any[]): AsyncState<T>
export function showToast(message: any, options?: { tone?: Tone; timeout?: number } | Tone): () => void
export function useToast(): {
  show: (message: any, options?: { tone?: Tone; timeout?: number } | Tone) => () => void
  info: (message: any, options?: { timeout?: number }) => () => void
  success: (message: any, options?: { timeout?: number }) => () => void
  warning: (message: any, options?: { timeout?: number }) => () => void
  error: (message: any, options?: { timeout?: number }) => () => void
}
export function useConfirm(): (options: string | { title?: any; message?: any; tone?: Tone; confirmLabel?: any; cancelLabel?: any }) => Promise<boolean>
