// Pending reload bookkeeping, shared by the configuration editor and every plugin reload
// entry point. Saving or activating a profile only persists it on the server; the running
// host keeps its old configuration until a reload, and the hot-update endpoint merges into
// the live config so it cannot delete keys. A per-plugin flag therefore records that the
// running plugin may not match the persisted configuration yet.
//
// The flag lives in this window's memory only. It survives navigating away from the
// configuration page and back, but not a page reload. Persisting it (as an earlier revision
// did with localStorage) kept it past the events that make it wrong: a backend restart
// relaunches auto-start plugins with the saved configuration, and plugins missing from the
// list snapshot could not be cleared by a bulk reload. Only the server knows which
// configuration a host is running; see #3192.
//
// Two windows are not expected to edit one plugin's configuration at the same time, and no
// attempt is made to detect or merge that: the later write simply wins.
//
// Writes are applied in arrival order: the last operation to report wins. A reload that
// finishes before an in-flight save may have read the pre-save configuration, so the later
// save still records the flag — a spurious hint costs one redundant reload, while a missing
// hint silently leaves the host on a stale configuration.

type PendingListener = (pluginId: string, pending: boolean) => void

const listeners = new Set<PendingListener>()
const flags = new Set<string>()
// Bumped on every write, so a start or reload can refuse to clear a flag that a save
// claimed while it was in flight.
const revisions = new Map<string, number>()

export function hasPendingReload(pluginId: string): boolean {
  return !!pluginId && flags.has(pluginId)
}

/** Identifies the flag as it stands now, for a later conditional clear. */
export function pendingReloadRevision(pluginId: string): number {
  return revisions.get(pluginId) ?? 0
}

/** Captures revisions for every plugin this window has observed before a bulk operation. */
export function pendingReloadRevisionSnapshot(): Map<string, number> {
  return new Map(revisions)
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
  if (pending) flags.add(pluginId)
  else flags.delete(pluginId)
  for (const listener of listeners) listener(pluginId, pending)
  return true
}

/** Observes flag changes made within this window, such as a reload from the plugin list. */
export function subscribePendingReload(listener: PendingListener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
