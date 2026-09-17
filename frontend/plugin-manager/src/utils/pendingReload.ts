// Pending reload bookkeeping, shared by the configuration editor and every plugin
// reload entry point. Saving or activating a profile only persists the mapping on
// the server; the running host keeps its old configuration until a reload, and the
// hot-update endpoint merges into the live config so it cannot delete keys. A
// per-plugin flag therefore records that the running plugin may not match the
// persisted configuration yet.

const STORAGE_KEY = 'neko-plugin-config-pending-reload'

type PendingRecord = Record<string, true>

// Mirrors storage so the flag still works when localStorage is unavailable or
// throws, which is why callers do not need their own fallback.
const inMemory: PendingRecord = {}

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage ?? undefined
  } catch {
    return undefined
  }
}

function readRecord(): PendingRecord | null {
  const store = storage()
  if (!store) return null
  let raw: string | null
  try {
    raw = store.getItem(STORAGE_KEY)
  } catch {
    return null
  }
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as PendingRecord) : {}
  } catch {
    // Corrupt content means "nothing pending" rather than "unavailable".
    return {}
  }
}

// Storage is authoritative when it works, so clearing it clears the flag; the
// in-memory mirror only serves contexts where storage is unavailable or throws.
export function hasPendingReload(pluginId: string): boolean {
  if (!pluginId) return false
  const record = readRecord()
  return record ? record[pluginId] === true : inMemory[pluginId] === true
}

export function setPendingReload(pluginId: string, pending: boolean): void {
  if (!pluginId) return
  if (pending) inMemory[pluginId] = true
  else delete inMemory[pluginId]
  const store = storage()
  if (!store) return
  try {
    const record = readRecord() ?? {}
    if (pending) record[pluginId] = true
    else delete record[pluginId]
    store.setItem(STORAGE_KEY, JSON.stringify(record))
  } catch {
    // The in-memory mirror still drives this session.
  }
}
