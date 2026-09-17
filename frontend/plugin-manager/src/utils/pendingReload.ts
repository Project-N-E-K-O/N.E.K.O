// Pending reload bookkeeping, shared by the configuration editor and every plugin
// reload entry point. Saving or activating a profile only persists the mapping on
// the server; the running host keeps its old configuration until a reload, and the
// hot-update endpoint merges into the live config so it cannot delete keys. A
// per-plugin flag therefore records that the running plugin may not match the
// persisted configuration yet.
//
// Each plugin owns its own storage key so that concurrent windows writing different
// plugins cannot overwrite each other's flag, and so a reserved plugin id such as
// "__proto__" is just part of a key rather than an object property.
//
// Writes are applied in arrival order: the last operation to report wins. A reload
// that finishes before an in-flight save may have read the pre-save configuration,
// so the later save still records the flag — a spurious hint costs one redundant
// reload, while a missing hint silently leaves the host on a stale configuration.
// Storage is authoritative while it works; the in-memory sets only serve contexts
// where storage is unavailable or where a write could not be persisted.

const KEY_PREFIX = 'neko-plugin-config-pending-reload:'
// An earlier revision of this feature (never released) stored every plugin in one
// JSON record under this key. It is migrated once so a browser that ran that
// revision does not silently lose its pending flags.
const LEGACY_KEY = 'neko-plugin-config-pending-reload'

export type PendingSource = 'local' | 'external'
type PendingListener = (pluginId: string, pending: boolean, source: PendingSource) => void

const listeners = new Set<PendingListener>()
// Mirrors the flag when storage is missing, and holds flags whose write was rejected.
const inMemory = new Set<string>()
const unpersisted = new Set<string>()
// Flags this window believes are set in storage, used to report `storage.clear()`.
const lastKnown = new Set<string>()
let storageListenerAttached = false

const keyFor = (pluginId: string) => KEY_PREFIX + pluginId

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage ?? undefined
  } catch {
    return undefined
  }
}

/** Returns the stored flag, or null when storage cannot be read. */
function readStored(pluginId: string): boolean | null {
  const store = storage()
  if (!store) return null
  try {
    return store.getItem(keyFor(pluginId)) !== null
  } catch {
    return null
  }
}

function rememberLocal(pluginId: string, pending: boolean): void {
  if (pending) {
    inMemory.add(pluginId)
    lastKnown.add(pluginId)
  } else {
    inMemory.delete(pluginId)
    lastKnown.delete(pluginId)
  }
}

function notify(pluginId: string, pending: boolean, source: PendingSource): void {
  for (const listener of listeners) listener(pluginId, pending, source)
}

// Another renderer window (or tab) changed a flag. `storage` only fires in the
// windows that did not write, so this is exactly the cross-window path.
function handleStorageEvent(event: Event): void {
  const { key, newValue } = event as StorageEvent
  if (key === null) {
    // storage.clear() removed every flag; report the ones this window knew about.
    const cleared = [...lastKnown]
    lastKnown.clear()
    inMemory.clear()
    for (const pluginId of cleared) notify(pluginId, false, 'external')
    return
  }
  if (!key?.startsWith(KEY_PREFIX)) return
  const pluginId = key.slice(KEY_PREFIX.length)
  if (!pluginId) return
  const pending = newValue !== null
  rememberLocal(pluginId, pending)
  notify(pluginId, pending, 'external')
}

// One-time best effort migration of the legacy aggregate record.
function migrateLegacyRecord(): void {
  const store = storage()
  if (!store) return
  try {
    const raw = store.getItem(LEGACY_KEY)
    if (raw === null) return
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') {
      for (const [pluginId, pending] of Object.entries(parsed as Record<string, unknown>)) {
        if (pluginId && pending === true && store.getItem(keyFor(pluginId)) === null)
          store.setItem(keyFor(pluginId), '1')
      }
    }
    store.removeItem(LEGACY_KEY)
  } catch {
    // A partially written or rejected migration leaves the current keys untouched.
  }
}

function attachStorageListener(): void {
  if (storageListenerAttached) return
  migrateLegacyRecord()
  try {
    globalThis.addEventListener?.('storage', handleStorageEvent)
    storageListenerAttached = true
    // Seed the known set so a later clear() reports the right plugins.
    const store = storage()
    if (!store) return
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index)
      if (key?.startsWith(KEY_PREFIX)) lastKnown.add(key.slice(KEY_PREFIX.length))
    }
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
  const stored = readStored(pluginId)
  if (stored === null) return inMemory.has(pluginId)
  return stored || unpersisted.has(pluginId)
}

export function setPendingReload(pluginId: string, pending: boolean): boolean {
  if (!pluginId) return false
  const store = storage()
  let persisted = false
  if (store) {
    try {
      if (pending) store.setItem(keyFor(pluginId), '1')
      else store.removeItem(keyFor(pluginId))
      persisted = true
    } catch {
      // A rejected write must not lose the flag: the mirror carries it instead.
      persisted = false
    }
  }
  if (persisted) unpersisted.delete(pluginId)
  else if (pending) unpersisted.add(pluginId)
  else unpersisted.delete(pluginId)
  rememberLocal(pluginId, pending)
  notify(pluginId, pending, 'local')
  return true
}

/** Observes flag changes made anywhere, including other windows and the store. */
export function subscribePendingReload(listener: PendingListener): () => void {
  listeners.add(listener)
  attachStorageListener()
  return () => {
    listeners.delete(listener)
    detachStorageListenerIfUnused()
  }
}
