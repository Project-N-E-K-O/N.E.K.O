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

// An earlier revision of this feature (only ever present on an unmerged branch) kept
// every plugin inside one JSON record under `neko-plugin-config-pending-reload`. That
// format is deliberately ignored rather than migrated: "key absent" already means
// both "never migrated" and "explicitly cleared", so a migration could resurrect a
// flag that an explicit clear had discarded, and no released version wrote it.
const KEY_PREFIX = 'neko-plugin-config-pending-reload:'

export type PendingSource = 'local' | 'external'
type PendingListener = (pluginId: string, pending: boolean, source: PendingSource) => void

const listeners = new Set<PendingListener>()
// Mirrors the flag when storage is missing, and holds flags whose write was rejected.
const inMemory = new Set<string>()
const unpersisted = new Set<string>()
// Flags this window believes are set in storage, used to report `storage.clear()`.
const lastKnown = new Set<string>()
// Identity of the flag this window set, so a conditional clear can still tell whether a
// newer write happened while `pendingReloadToken` has to answer from memory (storage
// missing, or the write rejected).
const tokens = new Map<string, string>()
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

// The stored value doubles as the identity of the flag that is currently set. A start or
// reload captures it before its request and clears the flag only while it is still the
// same one: a profile write that lands in the meantime describes a newer configuration
// than the just-started host can have read. Refusing such a clear can only keep a warning
// around, never drop one.
let writeCount = 0

function nextToken(): string {
  writeCount += 1
  return `${Date.now()}-${writeCount}-${Math.random().toString(36).slice(2, 10)}`
}

/** The value that currently marks `pluginId` as pending, or null when no flag is set. */
export function pendingReloadToken(pluginId: string): string | null {
  const store = storage()
  if (store) {
    try {
      const stored = store.getItem(keyFor(pluginId))
      if (stored !== null) return stored
    } catch {
      // Storage cannot be read; the mirror below is the only evidence left.
    }
  }
  return tokens.get(pluginId) ?? null
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
    // storage.clear() removed every persisted flag; report the ones this window knew
    // about. A flag whose write never reached storage only lives in this window, so
    // it survives the clear and its subscribers are not told otherwise.
    const cleared = [...lastKnown]
    lastKnown.clear()
    inMemory.clear()
    // A flag whose write never reached storage only lives in this window, so it survives
    // the clear and its subscribers are not told otherwise. Restore every such flag, not
    // just the ones this clear reported, or a second clear would silently drop them and a
    // later storage read failure would then report the plugin as up to date.
    for (const pluginId of unpersisted) inMemory.add(pluginId)
    for (const pluginId of cleared) {
      if (!unpersisted.has(pluginId)) notify(pluginId, false, 'external')
    }
    return
  }
  if (!key?.startsWith(KEY_PREFIX)) return
  const pluginId = key.slice(KEY_PREFIX.length)
  if (!pluginId) return
  const pending = newValue !== null
  rememberLocal(pluginId, pending)
  notify(pluginId, pending, 'external')
}

function attachStorageListener(): void {
  if (storageListenerAttached) return
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

export function setPendingReload(
  pluginId: string,
  pending: boolean,
  expectedToken?: string | null
): boolean {
  if (!pluginId) return false
  // Callers that clear a flag they captured pass the token they started from; anything
  // written since then belongs to a newer configuration and stays pending.
  if (expectedToken !== undefined && pendingReloadToken(pluginId) !== expectedToken) return false
  const store = storage()
  const token = pending ? nextToken() : null
  let persisted = false
  if (store) {
    try {
      if (token) store.setItem(keyFor(pluginId), token)
      else store.removeItem(keyFor(pluginId))
      persisted = true
    } catch {
      // A rejected write must not lose the flag: the mirror carries it instead.
      persisted = false
    }
  }
  if (token) tokens.set(pluginId, token)
  else tokens.delete(pluginId)
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
