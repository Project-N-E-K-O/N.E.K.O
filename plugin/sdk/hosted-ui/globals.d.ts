declare const h: (type: any, props: any, ...children: any[]) => any
declare const Fragment: any
declare const useState: <T>(initialValue: T | (() => T)) => [T, (next: T | ((previous: T) => T)) => T]
declare const useReducer: <S, A>(reducer: (state: S, action: A) => S, initialArg: S, init?: (value: S) => S) => [S, (action: A) => void]
declare const useEffect: (effect: () => void | (() => void), deps?: any[]) => void
declare const useLayoutEffect: (effect: () => void | (() => void), deps?: any[]) => void
declare const useMemo: <T>(factory: () => T, deps?: any[]) => T
declare const useCallback: <T extends (...args: any[]) => any>(callback: T, deps?: any[]) => T
declare const useRef: <T>(initialValue: T) => { current: T }
declare const useDebounce: <T>(value: T, delay?: number) => T
declare const useDebouncedState: <T>(initialValue: T, delay?: number) => [T, (next: T | ((previous: T) => T)) => T, T]
declare const useAsync: <T>(loader: () => Promise<T> | T, deps?: any[]) => { loading: boolean; error: any; data: T | undefined; reload: () => any }
declare const Page: any
declare const Card: any
declare const Section: any
declare const Heading: any
declare const Stack: any
declare const Grid: any
declare const Text: any
declare const Button: any
declare const ButtonGroup: any
declare const StatusBadge: any
declare const StatCard: any
declare const KeyValue: any
declare const DataTable: any
declare const Divider: any
declare const Toolbar: any
declare const ToolbarGroup: any
declare const Alert: any
declare const EmptyState: any
declare const ErrorBoundary: any
declare const Modal: any
declare const ConfirmDialog: any
declare const List: any
declare const Progress: any
declare const JsonView: any
declare const Field: any
declare const Input: any
declare const PasswordInput: any
declare const NumberInput: any
declare const Slider: any
declare const Select: any
declare const RadioGroup: any
declare const SegmentedControl: any
declare const Textarea: any
declare const Switch: any
declare const Checkbox: any
declare const CheckboxGroup: any
declare const Accordion: any
declare const Markdown: any
declare const ImageUpload: any
declare const ImagePreview: any
declare const Gallery: any
declare const FileDownload: any
declare const Form: any
declare const ActionButton: any
declare const RefreshButton: any
declare const ActionForm: any
declare const AsyncBlock: any
declare const InlineError: any
declare const CodeBlock: any
declare const Tip: any
declare const Warning: any
declare const Steps: any
declare const Step: any
declare const Tabs: any
declare const useI18n: any
declare const useForm: any
declare const useToast: any
declare const useConfirm: any
