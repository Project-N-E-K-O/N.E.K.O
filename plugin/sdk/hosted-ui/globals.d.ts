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
