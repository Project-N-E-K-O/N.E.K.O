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
  call: (actionId: string, args?: Record<string, any>, options?: { timeoutMs?: number }) => Promise<any>
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
export type ElementSize = { width: number; height: number }
export type ClipboardState = {
  write: (value: any) => Promise<boolean>
  read: () => Promise<string>
  copied: boolean
  error: any
}
export type AsyncState<T> = { loading: boolean; error: any; data: T | undefined; reload: () => any }
export type FormErrors<T extends Record<string, any>> = Partial<Record<keyof T | string, any>>
export type FormTouched<T extends Record<string, any>> = Partial<Record<keyof T | string, boolean>>
export type FormState<T extends Record<string, any>> = {
  values: T
  setValues: (next: T | ((previous: T) => T)) => T
  setField: {
    <K extends keyof T>(name: K, value: T[K]): T
    (name: string, value: any): T
  }
  field: {
    <K extends keyof T>(name: K): { value: T[K]; onChange: (value: T[K]) => T; onBlur: () => FormTouched<T>; error: any; touched: boolean }
    (name: string): { value: any; onChange: (value: any) => T; onBlur: () => FormTouched<T>; error: any; touched: boolean }
  }
  checkbox: {
    <K extends keyof T>(name: K): { checked: boolean; onChange: (value: boolean) => T; onBlur: () => FormTouched<T>; error: any; touched: boolean }
    (name: string): { checked: boolean; onChange: (value: boolean) => T; onBlur: () => FormTouched<T>; error: any; touched: boolean }
  }
  reset: (next?: T | (() => T)) => T
  touched: FormTouched<T>
  setTouched: (next: FormTouched<T> | ((previous: FormTouched<T>) => FormTouched<T>)) => FormTouched<T>
  setFieldTouched: (name: keyof T | string, value?: boolean) => FormTouched<T>
  errors: FormErrors<T>
  setErrors: (next: FormErrors<T> | ((previous: FormErrors<T>) => FormErrors<T>)) => FormErrors<T>
  setError: (name: keyof T | string, error: any) => FormErrors<T>
  clearError: (name: keyof T | string) => FormErrors<T>
  dirty: boolean
  isDirty: boolean
  submitCount: number
  validate: (validator?: (values: T) => FormErrors<T> | string | boolean | void) => boolean
  handleSubmit: (
    onValid?: (values: T, event?: Event) => any,
    onInvalid?: (errors: FormErrors<T>, values: T, event?: Event) => any
  ) => (event?: Event) => Promise<any>
}
export type ToastOptions = { tone?: Tone; timeout?: number; loadingTone?: Tone; successTone?: Tone; errorTone?: Tone; loading?: any; success?: any; error?: any }
export type ToastPromiseMessages<T = any> = {
  loading?: any
  success?: any | ((value: T) => any)
  error?: any | ((error: any) => any)
}
export type ToastApi = {
  show: (message: any, options?: ToastOptions | Tone) => () => void
  info: (message: any, options?: ToastOptions) => () => void
  success: (message: any, options?: ToastOptions) => () => void
  warning: (message: any, options?: ToastOptions) => () => void
  error: (message: any, options?: ToastOptions) => () => void
  promise: <T>(promise: Promise<T> | T, messages?: ToastPromiseMessages<T>, options?: ToastOptions) => Promise<T>
}

export type PluginSurfaceProps<State = Record<string, any>> = {
  plugin: Record<string, any>
  host?: {
    origin?: string
  }
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
  key?: any
  className?: string
  children?: any
}

export type DataTableColumn<T = Record<string, any>> = string | {
  key: keyof T | string
  label?: any
  render?: (row: T, index: number) => any
}

export type UiOption = string | {
  value: any
  label?: any
  title?: any
  disabled?: boolean
}

export type UploadedFileInfo = {
  name: string
  size: number
  type: string
}

export type ArtifactType = "image" | "audio" | "video" | "text" | "json" | "file" | "unknown"
export type ArtifactView = "preview" | "download" | "markdown" | "log" | "code" | "table" | "keyValue" | "raw" | string
export type ArtifactLike = {
  id?: string | number
  type?: ArtifactType | "markdown" | "log" | "table" | "folder"
  view?: ArtifactView
  role?: "input" | "output" | "reference" | "debug" | "intermediate" | string
  label?: any
  title?: any
  description?: any
  dataUrl?: string
  src?: string
  url?: string
  href?: string
  path?: string
  data?: any
  value?: any
  text?: string
  markdown?: string
  rows?: any[]
  columns?: Array<DataTableColumn<any>>
  name?: string
  filename?: string
  mime?: string
  size?: number
  durationMs?: number
  width?: number
  height?: number
  isDirectory?: boolean
  status?: "pending" | "running" | "done" | "error" | string
  error?: any
  metadata?: Record<string, any>
}
export type NormalizedArtifact = ArtifactLike & {
  type: ArtifactType
  view: ArtifactView
  label?: any
}

export function Page(props: CommonProps & { title?: any; subtitle?: any }): any
export function Card(props: CommonProps & { title?: any }): any
export function Section(props: CommonProps): any
export function Heading(props: CommonProps & { as?: string }): any
export function Container(props: CommonProps & { maxWidth?: number | string; width?: number | string; padding?: number | string }): any
export function Stack(props: CommonProps & { gap?: number | string }): any
export function Inline(props: CommonProps & {
  gap?: number | string
  align?: "start" | "center" | "end" | "stretch" | "baseline" | string
  justify?: "start" | "center" | "end" | "space-between" | "space-around" | "space-evenly" | string
  wrap?: boolean
}): any
export function Grid(props: CommonProps & { cols?: number; gap?: number | string }): any
export function Columns(props: CommonProps & { cols?: number; columns?: number; minWidth?: number | string; minColumnWidth?: number | string; gap?: number | string; fluid?: boolean }): any
export function Split(props: CommonProps & { ratio?: string; template?: string; direction?: "horizontal" | "vertical"; gap?: number | string; align?: string }): any
export function ScrollArea(props: CommonProps & { height?: number | string; maxHeight?: number | string; minHeight?: number | string; padding?: number | string; axis?: "x" | "y" | "both"; autoScroll?: boolean; deps?: any[]; scrollBehavior?: ScrollBehavior }): any
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
export function Modal(props: CommonProps & { open?: boolean; title?: any; footer?: any; closeOnBackdrop?: boolean; closeOnEscape?: boolean; lockScroll?: boolean; size?: "sm" | "md" | "lg" | "xl" | "full" | string; onClose?: () => void }): any
export function ConfirmDialog(props: CommonProps & { open?: boolean; title?: any; message?: any; tone?: Tone; confirmLabel?: any; cancelLabel?: any; closeOnBackdrop?: boolean; onConfirm?: () => void; onCancel?: () => void }): any
export function Tooltip(props: CommonProps & { content?: any; label?: any; title?: any; placement?: "top" | "bottom" | "left" | "right"; tabIndex?: number | string }): any
export function List<T = any>(props: CommonProps & { items?: T[]; render?: (item: T, index: number) => any }): any
export function Progress(props: CommonProps & { label?: any; value?: number; indeterminate?: boolean }): any
export function JsonView(props: CommonProps & { data?: any; value?: any }): any
export function Field(props: CommonProps & { label?: any; help?: any; error?: any; required?: boolean }): any
export function Input(props: CommonProps & { type?: string; value?: any; placeholder?: string; min?: number; max?: number; step?: number; invalid?: boolean; error?: any; disabled?: boolean; onChange?: (value: string) => void }): any
export function PasswordInput(props: CommonProps & { value?: any; placeholder?: string; invalid?: boolean; error?: any; disabled?: boolean; onChange?: (value: string) => void }): any
export function NumberInput(props: CommonProps & { value?: number | ""; placeholder?: string; min?: number; max?: number; step?: number; invalid?: boolean; error?: any; disabled?: boolean; onChange?: (value: number | string) => void }): any
export function Slider(props: CommonProps & { value?: number; min?: number; max?: number; step?: number; showValue?: boolean; disabled?: boolean; onChange?: (value: number) => void }): any
export function Select(props: CommonProps & { value?: any; options?: UiOption[]; invalid?: boolean; error?: any; disabled?: boolean; onChange?: (value: any) => void }): any
export function RadioGroup(props: CommonProps & { name?: string; value?: any; options?: UiOption[]; disabled?: boolean; onChange?: (value: any) => void }): any
export function SegmentedControl(props: CommonProps & { value?: any; options?: UiOption[]; disabled?: boolean; onChange?: (value: any) => void }): any
export function Textarea(props: CommonProps & { value?: any; placeholder?: string; invalid?: boolean; error?: any; disabled?: boolean; onChange?: (value: string) => void }): any
export function Switch(props: CommonProps & { checked?: boolean; label?: any; invalid?: boolean; error?: any; disabled?: boolean; onChange?: (value: boolean) => void }): any
export function Checkbox(props: CommonProps & { checked?: boolean; value?: boolean; label?: any; invalid?: boolean; error?: any; disabled?: boolean; onChange?: (value: boolean) => void }): any
export function CheckboxGroup(props: CommonProps & { value?: any[]; options?: UiOption[]; disabled?: boolean; onChange?: (value: any[]) => void }): any
export function Accordion(props: CommonProps & { id?: string; title?: any; label?: any; open?: boolean }): any
/** Styled preformatted text container for markdown-like content. It does not parse Markdown into HTML. */
export function Markdown(props: CommonProps & { source?: any; text?: any }): any
export function ImageUpload(props: CommonProps & { value?: ArtifactLike | string; label?: any; placeholder?: any; alt?: string; accept?: string; maxBytes?: number; compact?: boolean; variant?: string; onChange?: (artifact: ArtifactLike) => void; onError?: (error: any) => void }): any
export function AudioUpload(props: CommonProps & { value?: ArtifactLike | string; label?: any; placeholder?: any; accept?: string; maxBytes?: number; compact?: boolean; variant?: string; onChange?: (artifact: ArtifactLike) => void; onError?: (error: any) => void }): any
export function VideoUpload(props: CommonProps & { value?: ArtifactLike | string; label?: any; placeholder?: any; accept?: string; maxBytes?: number; compact?: boolean; variant?: string; onChange?: (artifact: ArtifactLike) => void; onError?: (error: any) => void }): any
export function ImagePreview(props: CommonProps & { artifact?: ArtifactLike; src?: string; value?: string | ArtifactLike; label?: any; caption?: any; alt?: string; emptyText?: any; placeholder?: any }): any
export function AudioPlayer(props: CommonProps & { artifact?: ArtifactLike; src?: string; value?: string | ArtifactLike; label?: any; caption?: any; controls?: boolean; preload?: string; emptyText?: any }): any
export function VideoPlayer(props: CommonProps & { artifact?: ArtifactLike; src?: string; value?: string | ArtifactLike; label?: any; caption?: any; poster?: string; controls?: boolean; preload?: string; emptyText?: any }): any
export function Gallery<T = any>(props: CommonProps & { items?: T[]; columns?: number; cols?: number; emptyText?: any; onSelect?: (item: T, index: number) => void }): any
export function FileDownload(props: CommonProps & { href?: string; url?: string; dataUrl?: string; path?: string; filename?: string; label?: any; copiedLabel?: any; tone?: Tone; target?: string; openExternal?: boolean }): any
export function TextBlock(props: CommonProps & { text?: any; value?: any }): any
export function LogViewer(props: CommonProps & { text?: any; value?: any; autoScroll?: boolean; deps?: any[]; scrollBehavior?: ScrollBehavior }): any
export function JsonEditorLite(props: CommonProps & { value?: any; data?: any; mode?: "json" | "text"; onChange?: (value: any) => void }): any
export function ArtifactRenderer(props: CommonProps & { artifact?: ArtifactLike; item?: ArtifactLike; value?: ArtifactLike; render?: (artifact: NormalizedArtifact) => any }): any
export function ArtifactCard(props: CommonProps & { artifact?: ArtifactLike; item?: ArtifactLike; value?: ArtifactLike; label?: any; renderArtifact?: (artifact: NormalizedArtifact) => any }): any
export function ArtifactList(props: CommonProps & { items?: ArtifactLike[]; layout?: "list" | "grid"; empty?: any; emptyText?: any; cardClassName?: string | ((item: ArtifactLike, index: number) => string); renderArtifact?: (artifact: NormalizedArtifact) => any }): any
export function normalizeArtifact(value: any): NormalizedArtifact
export function detectArtifactType(value: any): ArtifactType
export function Form(props: CommonProps & { onSubmit?: (event: Event) => void | Promise<void> }): any
export function FormSection(props: CommonProps & { title?: any; description?: any }): any
export function FormActions(props: CommonProps & { align?: "start" | "end" }): any
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
export function useElementSize<T extends Element = Element>(ref: RefObject<T | null>): ElementSize
export function useScrollIntoView<T extends Element = Element>(ref: RefObject<T | null>, defaults?: ScrollIntoViewOptions): (options?: ScrollIntoViewOptions) => void
export function useScrollToBottom<T extends Element = Element>(ref: RefObject<T | null>, deps?: any[], options?: { enabled?: boolean; behavior?: ScrollBehavior }): void
export function useClipboard(): ClipboardState
export function useLocalState<T>(key: string, initialValue: T | (() => T)): [T, LocalStateSetter<T>]
export function useDebounce<T>(value: T, delay?: number): T
export function useDebouncedState<T>(initialValue: T, delay?: number): [T, StateSetter<T>, T]
export function useForm<T extends Record<string, any>>(initialValues: T | (() => T), options?: { validate?: (values: T) => FormErrors<T> | string | boolean | void }): FormState<T>
export function useAsync<T>(loader: () => Promise<T> | T, deps?: any[]): AsyncState<T>
export const showToast: ((message: any, options?: ToastOptions | Tone) => () => void) & { promise: ToastApi["promise"] }
export function useToast(): ToastApi
export function useConfirm(): (options: string | { title?: any; message?: any; tone?: Tone; confirmLabel?: any; cancelLabel?: any }) => Promise<boolean>
