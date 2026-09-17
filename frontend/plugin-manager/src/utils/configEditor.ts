// Configuration view helpers. They never persist data or alter backend merge rules.

const hasOwn = (value: object, key: PropertyKey) => Object.prototype.hasOwnProperty.call(value, key)

// Configuration keys are data, including the literal key "__proto__".
export function setConfigKey(target: object, key: string, value: any): void {
  setOwn(target, key, value)
}

function setOwn(target: object, key: PropertyKey, value: any) {
  Object.defineProperty(target, key, {
    value,
    writable: true,
    enumerable: true,
    configurable: true,
  })
}

export type ConfigObject = Record<string, any>
export type ConfigFilter = 'all' | 'dirty' | 'configured'
export interface ConfigChange {
  path: string[]
  before: any
  after: any
  beforePresent: boolean
  afterPresent: boolean
}

export function isConfigObject(value: any): value is ConfigObject {
  return Object.prototype.toString.call(value) === '[object Object]'
}

export function configEqual(a: any, b: any): boolean {
  if (Object.is(a, b)) return true
  if (a instanceof Date && b instanceof Date) return a.getTime() === b.getTime()
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((v, i) => configEqual(v, b[i]))
    )
  }
  if (!isConfigObject(a) || !isConfigObject(b)) return false
  const keys = Object.keys(a)
  return (
    keys.length === Object.keys(b).length &&
    keys.every((k) => hasOwn(b, k) && configEqual(a[k], b[k]))
  )
}

// Arrays are one replacement, not a sparse set of per-index profile overrides.
export function configChanges(
  before: ConfigObject,
  after: ConfigObject,
  path: string[] = []
): ConfigChange[] {
  const result: ConfigChange[] = []
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    if (path.length === 0 && key === 'plugin') continue
    const a = before[key],
      b = after[key]
    const hasA = hasOwn(before, key),
      hasB = hasOwn(after, key)
    if (hasA === hasB && configEqual(a, b)) continue
    if (
      (isConfigObject(a) || !hasA) &&
      (isConfigObject(b) || !hasB) &&
      Object.keys(a || {}).length + Object.keys(b || {}).length > 0
    ) {
      result.push(...configChanges(a || {}, b || {}, [...path, key]))
    } else {
      result.push({
        path: [...path, key],
        before: a,
        after: b,
        beforePresent: hasA,
        afterPresent: hasB,
      })
    }
  }
  return result
}

// Replays a change list onto another base. Used to rebase locally edited drafts
// onto content another window saved, so saving afterwards cannot discard it.
export function applyConfigChanges(base: ConfigObject, changes: ConfigChange[]): ConfigObject {
  const next = deepClone(base)
  for (const change of changes) {
    let target: any = next
    for (const key of change.path.slice(0, -1)) {
      if (!hasOwn(target, key) || !isMapping(target[key])) setOwn(target, key, {})
      target = target[key]
    }
    const key = change.path[change.path.length - 1]
    if (key === undefined) continue
    if (change.afterPresent) setOwn(target, key, deepClone(change.after))
    else delete target[key]
  }
  return next
}

export function configValueAt(config: any, path: string[]): any {
  return path.reduce(
    (value, key) => (value != null && hasOwn(value, key) ? value[key] : undefined),
    config
  )
}

export function pathContains(parent: string[], child: string[]): boolean {
  return parent.length <= child.length && parent.every((key, index) => key === child[index])
}

export function hasConfigChangesAt(changes: ConfigChange[], path: string[]): boolean {
  return changes.some(
    (change) => pathContains(path, change.path) || pathContains(change.path, path)
  )
}

export function configuredFieldCount(value: any, root = false): number {
  if (!isConfigObject(value) || Object.keys(value).length === 0) return 1
  return Object.entries(value)
    .filter(([key]) => !root || key !== 'plugin')
    .reduce((n, [, child]) => n + configuredFieldCount(child), 0)
}

export function configValueText(value: any): string {
  if (typeof value === 'string') return value === '' ? '""' : value
  return JSON.stringify(value, null, 2) ?? '—'
}

// Preserve literal keys; display/search paths must never be split to write data.
export function restoreConfigPath(
  draft: ConfigObject,
  original: ConfigObject,
  path: string[]
): ConfigObject {
  const next = deepClone(draft)
  function restore(target: any, source: any, depth: number) {
    const key = path[depth]!
    const sourceHasKey = source != null && hasOwn(source, key)
    if (depth === path.length - 1) {
      if (sourceHasKey) setOwn(target, key, deepClone(source[key]))
      else delete target[key]
      return
    }
    if (!hasOwn(target, key) || !isConfigObject(target[key])) setOwn(target, key, {})
    restore(target[key], sourceHasKey ? source[key] : undefined, depth + 1)
    if (!Object.keys(target[key]).length && !sourceHasKey) delete target[key]
  }
  if (path.length) restore(next, original, 0)
  return next
}

export function configNodeMatches(
  overlay: any,
  baseline: any,
  path: string[],
  query: string,
  filter: ConfigFilter,
  changes: ConfigChange[],
  replacement = false
): boolean {
  const value = overlay !== undefined ? overlay : baseline
  if (isConfigObject(value)) {
    const a = isConfigObject(overlay) ? overlay : {}
    const b = !replacement && isConfigObject(baseline) ? baseline : {}
    const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].filter(
      (k) => path.length || k !== 'plugin'
    )
    // Test the current path before recursing, so section names match themselves
    const matchesState =
      filter === 'all' ||
      (filter === 'configured' && overlay !== undefined) ||
      (filter === 'dirty' && hasConfigChangesAt(changes, path))
    const matchesQuery = !query || path.join('.').toLowerCase().includes(query.toLowerCase())
    // The root must not match itself merely because an overlay object exists, but
    // with nothing filtered out it has to stay visible: a config holding only
    // protected metadata would otherwise hide the form and its Add field action.
    const rootUnfiltered = path.length === 0 && filter === 'all' && !query
    if ((path.length > 0 || rootUnfiltered) && matchesState && matchesQuery) return true
    if (keys.length)
      return keys.some((k) =>
        configNodeMatches(a[k], b[k], [...path, k], query, filter, changes, replacement)
      )
    return false
  }
  const matchesState =
    filter === 'all' ||
    (filter === 'configured' && overlay !== undefined) ||
    (filter === 'dirty' && hasConfigChangesAt(changes, path))
  // Lists stay intact while filtering; searching any item can reveal the list.
  const matchesQuery =
    path.join('.').toLowerCase().includes(query.toLowerCase()) ||
    (Array.isArray(value) &&
      value.some((item, i) =>
        configNodeMatches(item, undefined, [...path, String(i)], query, 'all', changes, true)
      ))
  return matchesState && matchesQuery
}

function cloneDeep<T>(input: T, seen = new WeakMap<object, any>()): T {
  if (input === null || typeof input !== 'object') return input

  if (input instanceof Date) return new Date(input.getTime()) as any
  if (input instanceof RegExp) return new RegExp(input.source, input.flags) as any

  if (seen.has(input as any)) return seen.get(input as any)

  if (Array.isArray(input)) {
    const out: any[] = []
    seen.set(input as any, out)
    for (const item of input as any[]) out.push(cloneDeep(item, seen))
    return out as any
  }

  if (input instanceof Map) {
    const out = new Map()
    seen.set(input as any, out)
    for (const [k, v] of input.entries())
      out.set(cloneDeep(k as any, seen), cloneDeep(v as any, seen))
    return out as any
  }

  if (input instanceof Set) {
    const out = new Set()
    seen.set(input as any, out)
    for (const v of input.values()) out.add(cloneDeep(v as any, seen))
    return out as any
  }

  const proto = Object.getPrototypeOf(input)
  const out = Object.create(proto)
  seen.set(input as any, out)
  for (const key of Reflect.ownKeys(input as any)) {
    const desc = Object.getOwnPropertyDescriptor(input as any, key)
    if (!desc) continue
    if ('value' in desc) {
      desc.value = cloneDeep((input as any)[key], seen)
    }
    Object.defineProperty(out, key, desc)
  }
  return out
}

export function deepClone<T>(v: T): T {
  const sc = (globalThis as any).structuredClone as undefined | ((x: any) => any)
  if (typeof sc === 'function') {
    try {
      return sc(v) as T
    } catch {
      // fall through
    }
  }
  return cloneDeep(v)
}

// Mirrors plugin/server/infrastructure/config_merge.py. Markers are ordinary data
// except while merging into a table that already exists in the base, which is why
// they are only interpreted here and never by `applyProfileOverlay`.
const DELETE_MARKER = '__DELETE__'
const REPLACE_MARKER = '__replace__'

function isMapping(value: any): value is ConfigObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function replacementTable(value: ConfigObject): ConfigObject {
  const table: ConfigObject = {}
  for (const [key, child] of Object.entries(value))
    if (key !== REPLACE_MARKER) setOwn(table, key, deepClone(child))
  return table
}

function deepMerge(base: ConfigObject, updates: ConfigObject): ConfigObject {
  const out: ConfigObject = { ...base }
  for (const [key, value] of Object.entries(updates)) {
    if (value === DELETE_MARKER) {
      delete out[key]
      continue
    }
    if (isMapping(value)) {
      if (value[REPLACE_MARKER] === true) {
        setOwn(out, key, replacementTable(value))
        continue
      }
      const current = hasOwn(out, key) ? out[key] : undefined
      if (isMapping(current)) {
        // An explicit empty table clears that table, matching the server merge.
        setOwn(out, key, Object.keys(value).length === 0 ? {} : deepMerge(current, value))
        continue
      }
      setOwn(out, key, deepClone(value))
      continue
    }
    setOwn(out, key, deepClone(value))
  }
  return out
}

export function applyProfileOverlay(base: any, overlay: any): any {
  if (!base && !overlay) return null
  if (!overlay) return deepClone(base)
  if (!base) return deepClone(overlay)
  const result: any = deepClone(base)
  for (const [k, v] of Object.entries(overlay)) {
    // Profile cannot modify the 'plugin' section; skip it — shown only in JSON preview
    if (k === 'plugin') continue
    // Only a table that already exists in the base is merged, so markers and empty
    // tables keep their literal meaning for any other key.
    const current = hasOwn(result, k) ? result[k] : undefined
    if (isMapping(current) && isMapping(v)) setOwn(result, k, deepMerge(current, v))
    else setOwn(result, k, deepClone(v))
  }
  return result
}
