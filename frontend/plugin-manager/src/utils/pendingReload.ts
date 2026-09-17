// Pending reload bookkeeping, shared by the configuration editor and every plugin
// reload entry point. Saving or activating a profile only persists the mapping on
// the server; the running host keeps its old configuration until a reload, and the
// hot-update endpoint merges into the live config so it cannot delete keys. A
// per-plugin flag therefore records that the running plugin may not match the
// persisted configuration yet.
//
// Writes are applied in arrival order: the last operation to report wins. A reload
// that finishes before an in-flight save may have read the pre-save configuration,
// so the later save still records the flag — a spurious hint costs one redundant
// reload, while a missing hint silently leaves the host on a stale configuration.
// Storage is authoritative while it works, so clearing it clears the flag; the
// in-memory mirror only serves contexts where storage is missing or failing.

const STORAGE_KEY = 'neko-plugin-config-pending-reload'

type PendingRecord = Record<string, true>

const inMemory = new Map<string, true>()
const listeners = new Set<(pluginId: string, pending: boolean) => void>()
let storageWritable = true
// Last storage content this window saw, used to diff changes made elsewhere.
let lastStored: PendingRecord = {}
let storageListenerAttached = false

const hasOwn = (value: object, key: PropertyKey) => Object.prototype.hasOwnProperty.call(value, key)

// A plugin id may literally be "__proto__", so pending keys are always written as
// own data properties instead of through assignment.
function setOwn(target: object, key: string, value: true): void {
  Object.defineProperty(target, key, {
    value,
    writable: true,
    enumerable: true,
    configurable: true,
  })
}

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage ?? undefined
  } catch {
    return undefined
  }
}

function parseRecord(raw: string | null): PendingRecord {
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as PendingRecord) : {}
  } catch {
    // Corrupt content means "nothing pending" rather than "unavailable".
    return {}
  }
}

/** Returns the stored record, or null when storage is unusable. */
function readStored(): PendingRecord | null {
  const store = storage()
  if (!store || !storageWritable) return null
  let raw: string | null
  try {
    raw = store.getItem(STORAGE_KEY)
  } catch {
    storageWritable = false
    return null
  }
  lastStored = parseRecord(raw)
  return lastStored
}

function writeStored(pluginId: string, pending: boolean): void {
  const store = storage()
  if (!store || !storageWritable) return
  try {
    const record = readStored() ?? {}
    if (pending) setOwn(record, pluginId, true)
    else delete record[pluginId]
    store.setItem(STORAGE_KEY, JSON.stringify(record))
    lastStored = record
  } catch {
    // Quota or policy failures must not lose the flag: from here on the mirror is
    // the source of truth for this session.
    storageWritable = false
  }
}

function notify(pluginId: string, pending: boolean): void {
  if (pending) inMemory.set(pluginId, true)
  else inMemory.delete(pluginId)
  for (const listener of listeners) listener(pluginId, pending)
}

// Another renderer window (or tab) changed the shared flag. `storage` only fires in
// the windows that did not write, so this is exactly the cross-window path.
function handleStorageEvent(event: StorageEvent): void {
  if (event.key && event.key !== STORAGE_KEY) return
  const previous = lastStored
  const next = parseRecord(event.newValue)
  lastStored = next
  for (const pluginId of new Set([...Object.keys(previous), ...Object.keys(next)])) {
    const before = hasOwn(previous, pluginId)
    const after = hasOwn(next, pluginId)
    if (before !== after) notify(pluginId, after)
  }
}

function attachStorageListener(): void {
  if (storageListenerAttached) return
  try {
    globalThis.addEventListener?.('storage', handleStorageEvent)
    storageListenerAttached = true
    readStored()
  } catch {
    // Listening is best effort; the flag still works within this window.
  }
}

function detachStorageListenerIfUnused(): void {
  if (!storageListenerAttached || listeners.size) return
  try {
    globalThis.removeEventListener?.('storage', handleStorageEvent)
  } catch {
    // Ignore teardown failures.
  }
  storageListenerAttached = false
}

export function hasPendingReload(pluginId: string): boolean {
  if (!pluginId) return false
  const record = readStored()
  return record ? hasOwn(record, pluginId) : inMemory.has(pluginId)
}

export function setPendingReload(pluginId: string, pending: boolean): boolean {
  if (!pluginId) return false
  writeStored(pluginId, pending)
  notify(pluginId, pending)
  return true
}

/** Observes flag changes made anywhere, including other windows and the store. */
export function subscribePendingReload(
  listener: (pluginId: string, pending: boolean) => void
): () => void {
  listeners.add(listener)
  attachStorageListener()
  return () => {
    listeners.delete(listener)
    detachStorageListenerIfUnused()
  }
}
