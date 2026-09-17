// Pending reload bookkeeping, shared by the configuration editor and every plugin
// reload entry point. Saving or activating a profile only persists the mapping on
// the server; the running host keeps its old configuration until a reload, and the
// hot-update endpoint merges into the live config so it cannot delete keys. A
// per-plugin flag therefore records that the running plugin may not match the
// persisted configuration yet.

const STORAGE_KEY = 'neko-plugin-config-pending-reload'

type PendingRecord = Record<string, true>

function readRecord(): PendingRecord {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed === 'object' ? (parsed as PendingRecord) : {}
  } catch {
    // Storage can be unavailable; callers fall back to in-memory state.
    return {}
  }
}

export function hasPendingReload(pluginId: string): boolean {
  return readRecord()[pluginId] === true
}

export function setPendingReload(pluginId: string, pending: boolean): void {
  if (!pluginId) return
  try {
    const record = readRecord()
    if (pending) record[pluginId] = true
    else delete record[pluginId]
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(record))
  } catch {
    // Best effort only.
  }
}
