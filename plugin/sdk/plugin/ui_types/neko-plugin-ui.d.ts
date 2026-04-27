declare module "@neko/plugin-ui" {
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
    t: (key: string) => string
    api: {
      call: (actionId: string, args?: Record<string, any>) => Promise<any>
      refresh: () => Promise<any>
    }
  }

  export type CommonProps = {
    className?: string
    children?: any
  }

  export function Page(props: CommonProps & { title?: any; subtitle?: any }): any
  export function Card(props: CommonProps & { title?: any }): any
  export function Section(props: CommonProps): any
  export function Heading(props: CommonProps & { as?: string }): any
  export function Stack(props: CommonProps & { gap?: number }): any
  export function Grid(props: CommonProps & { cols?: number; gap?: number }): any
  export function Text(props: CommonProps): any
  export function Button(props: CommonProps & { tone?: Tone; variant?: Tone; type?: string; onClick?: () => void | Promise<void> }): any
  export function ButtonGroup(props: CommonProps): any
  export function StatusBadge(props: CommonProps & { tone?: Tone; status?: Tone | string; label?: any }): any
  export function StatCard(props: CommonProps & { label?: any; value?: any }): any
  export function KeyValue(props: CommonProps & { data?: Record<string, any>; items?: Array<{ key?: string; label?: any; value?: any }> }): any
  export function DataTable<T = Record<string, any>>(props: CommonProps & {
    data?: T[]
    columns?: Array<string | { key: keyof T | string; label?: any }>
    rowKey?: keyof T | string
    selectedKey?: any
    onSelect?: (row: T, index: number) => void
  }): any
  export function Divider(): any
  export function Toolbar(props: CommonProps): any
  export function ToolbarGroup(props: CommonProps): any
  export function Alert(props: CommonProps & { tone?: Tone; message?: any }): any
  export function EmptyState(props: CommonProps & { title?: any; description?: any }): any
  export function List<T = any>(props: CommonProps & { items?: T[]; render?: (item: T) => any }): any
  export function Progress(props: CommonProps & { label?: any; value?: number }): any
  export function JsonView(props: CommonProps & { data?: any; value?: any }): any
  export function Field(props: CommonProps & { label?: any; help?: any }): any
  export function Input(props: CommonProps & { value?: any; placeholder?: string; onChange?: (value: string) => void }): any
  export function Select(props: CommonProps & { value?: any; options?: Array<string | { value: any; label?: any }>; onChange?: (value: any) => void }): any
  export function Textarea(props: CommonProps & { value?: any; placeholder?: string; onChange?: (value: string) => void }): any
  export function Switch(props: CommonProps & { checked?: boolean; label?: any; onChange?: (value: boolean) => void }): any
  export function Form(props: CommonProps & { onSubmit?: (event: Event) => void | Promise<void> }): any
  export function ActionButton(props: CommonProps & {
    action?: HostedAction
    actionId?: string
    label?: any
    tone?: Tone
    values?: Record<string, any>
    args?: Record<string, any>
    onResult?: (result: any) => void
    onError?: (error: Error) => void
  }): any
  export function RefreshButton(props: CommonProps & { label?: any; tone?: Tone; onRefresh?: () => void; onError?: (error: Error) => void }): any
  export function ActionForm(props: CommonProps & { action?: HostedAction; submitLabel?: any; onResult?: (result: any) => void; onError?: (error: Error) => void }): any
  export function CodeBlock(props: CommonProps): any
  export function Tip(props: CommonProps): any
  export function Warning(props: CommonProps): any
  export function Steps(props: CommonProps): any
  export function Step(props: CommonProps & { index?: any; title?: any }): any
  export function Tabs(props: CommonProps & { items?: Array<{ id?: string; label?: any; title?: any; content?: any }> }): any
  export function useI18n(): { t: (key: string) => string; locale: string }
}

declare const h: (type: any, props: any, ...children: any[]) => any
declare const Fragment: (props: { children?: any }) => any
declare const api: {
  call: (actionId: string, args?: Record<string, any>) => Promise<any>
  refresh: () => Promise<any>
}
