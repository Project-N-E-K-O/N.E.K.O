// Pending reload bookkeeping, shared by the configuration editor and every plugin
// reload entry point. Saving or activating a profile only persists the mapping on
// the server; the running host keeps its old configuration until a reload, and the
// hot-update endpoint merges into the live config so it cannot delete keys. A
// per-plugin flag therefore records that the running plugin may not match the
// persisted configuration yet.
//
// Two rules keep the flag honest:
//  - A reload or a real start supersedes any edit that was still in flight, so a
//    write made for an older revision is dropped instead of resurrecting the flag.
//  - Storage is authoritative while it works, so clearing it clears the flag; the
//    in-memory mirror only serves contexts where storage is missing or failing.

const STORAGE_KEY = 'neko-plugin-config-pending-reload'

type PendingRecord = Record<string, true>

const inMemory = new Map<string, true>()
const revisions = new Map<string, number>()
const listeners = new Set<(pluginId: string, pending: boolean) => void>()
let storageWritable = true

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

function readRecord(): PendingRecord | null {
  const store = storage()
  if (!store || !storageWritable) return null
  let raw: string | null
  try {
    raw = store.getItem(STORAGE_KEY)
  } catch {
    storageWritable = false
    return null
  }
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as PendingRecord) : {}
  } catch {
    // Corrupt content means "nothing pending" rather than "unavailable".
    return {}
  }
}

function writeRecord(pluginId: string, pending: boolean): void {
  const store = storage()
  if (!store || !storageWritable) return
  try {
    const record = readRecord() ?? {}
    if (pending) setOwn(record, pluginId, true)
    else delete record[pluginId]
    store.setItem(STORAGE_KEY, JSON.stringify(record))
  } catch {
    // Quota or policy failures must not lose the flag: from here on the mirror is
    // the source of truth for this session.
    storageWritable = false
  }
}

function notify(pluginId: string, pending: boolean): void {
  for (const listener of listeners) listener(pluginId, pending)
}

/** Current revision for a plugin; capture it before starting an edit. */
export function pendingRevision(pluginId: string): number {
  return revisions.get(pluginId) ?? 0
}

export function hasPendingReload(pluginId: string): boolean {
  if (!pluginId) return false
  const record = readRecord()
  return record ? hasOwn(record, pluginId) : inMemory.has(pluginId)
}

/**
 * Records or clears the flag. `revision` defaults to the current one; pass the
 * revision captured when an operation started so a lifecycle action that happened
 * meanwhile wins instead of being overwritten by the late result.
 */
export function setPendingReload(pluginId: string, pending: boolean, revision?: number): boolean {
  if (!pluginId) return false
  if (revision !== undefined && revision !== pendingRevision(pluginId)) return false
  revisions.set(pluginId, pendingRevision(pluginId) + 1)
  if (pending) inMemory.set(pluginId, true)
  else inMemory.delete(pluginId)
  writeRecord(pluginId, pending)
  notify(pluginId, pending)
  return true
}

/** Observes flag changes made anywhere, including by the plugin store. */
export function subscribePendingReload(
  listener: (pluginId: string, pending: boolean) => void
): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
