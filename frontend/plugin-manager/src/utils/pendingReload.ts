// Pending reload bookkeeping, shared by the configuration editor and every plugin reload
// entry point. Saving or activating a profile only persists it on the server; the running
// host keeps its old configuration until a reload, and the hot-update endpoint merges into
// the live config so it cannot delete keys. A per-plugin flag therefore records that the
// running plugin may not match the persisted configuration yet.
//
// The flag belongs to this window. Two windows are not expected to edit one plugin's
// configuration at the same time, and no attempt is made to detect or merge that: the later
// write simply wins. Keeping the flag local is what keeps the rest of this module small —
// there is no cross-window event to handle and no remote writer to reconcile with.
//
// Writes are applied in arrival order: the last operation to report wins. A reload that
// finishes before an in-flight save may have read the pre-save configuration, so the later
// save still records the flag — a spurious hint costs one redundant reload, while a missing
// hint silently leaves the host on a stale configuration.

const KEY_PREFIX = 'neko-plugin-config-pending-reload:'
const FLAG_VALUE = '1'

type PendingListener = (pluginId: string, pending: boolean) => void

const listeners = new Set<PendingListener>()
// The flags this window set, and the plugins it has an opinion about. Once a plugin is
// known here this module answers from memory, so a removal that storage refused cannot
// resurrect the flag.
const flags = new Set<string>()
const known = new Set<string>()
// Bumped on every write, so a start or reload can refuse to clear a flag that a save
// claimed while it was in flight.
const revisions = new Map<string, number>()

const keyFor = (pluginId: string) => KEY_PREFIX + pluginId

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage ?? undefined
  } catch {
    return undefined
  }
}

/** Reads the flag an earlier session left in storage, or null when it cannot be read. */
function readStored(pluginId: string): boolean | null {
  const store = storage()
  if (!store) return null
  try {
    return store.getItem(keyFor(pluginId)) !== null
  } catch {
    return null
  }
}

// Mirrored so the hint survives a page reload. This window is the only writer, so the
// stored value can never disagree with `flags` in a way that matters.
function persist(pluginId: string, pending: boolean): void {
  const store = storage()
  if (!store) return
  try {
    if (pending) store.setItem(keyFor(pluginId), FLAG_VALUE)
    else store.removeItem(keyFor(pluginId))
  } catch {
    // A rejected write is not fatal: this window answers from `flags`, and the only thing
    // lost is the hint surviving a page reload.
  }
}

export function hasPendingReload(pluginId: string): boolean {
  if (!pluginId) return false
  if (known.has(pluginId)) return flags.has(pluginId)
  return readStored(pluginId) ?? false
}

/** Identifies the flag as it stands now, for a later conditional clear. */
export function pendingReloadRevision(pluginId: string): number {
  return revisions.get(pluginId) ?? 0
}

/** The plugins this window currently flags, so a caller can capture their revisions. */
export function pendingReloadPlugins(): string[] {
  return [...flags]
}

/**
 * Records or clears the flag. `expectedRevision` is what a start or reload captured before
 * its request: a save that landed since then describes a configuration that host cannot
 * have read, so its flag stays. Refusing that clear only ever keeps a warning around.
 */
export function setPendingReload(
  pluginId: string,
  pending: boolean,
  expectedRevision?: number
): boolean {
  if (!pluginId) return false
  if (expectedRevision !== undefined && pendingReloadRevision(pluginId) !== expectedRevision)
    return false
  revisions.set(pluginId, pendingReloadRevision(pluginId) + 1)
  known.add(pluginId)
  if (pending) flags.add(pluginId)
  else flags.delete(pluginId)
  persist(pluginId, pending)
  for (const listener of listeners) listener(pluginId, pending)
  return true
}

/** Observes flag changes made within this window, such as a reload from the plugin list. */
export function subscribePendingReload(listener: PendingListener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
