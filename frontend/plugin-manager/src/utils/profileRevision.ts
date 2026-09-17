// Cross-window notification that a profile was persisted somewhere else. The
// pending-reload flag cannot carry this: saving a profile that is not active changes
// no running configuration, yet other windows still hold stale drafts for it and
// would overwrite the saved fields when they save next.

const KEY_PREFIX = 'neko-plugin-config-profile-revision:'

const listeners = new Set<(pluginId: string) => void>()
let listenerAttached = false

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage ?? undefined
  } catch {
    return undefined
  }
}

function handleStorageEvent(event: Event): void {
  const { key } = event as StorageEvent
  if (!key?.startsWith(KEY_PREFIX)) return
  const pluginId = key.slice(KEY_PREFIX.length)
  if (pluginId) for (const listener of listeners) listener(pluginId)
}

function attachListener(): void {
  if (listenerAttached) return
  try {
    globalThis.addEventListener?.('storage', handleStorageEvent)
    listenerAttached = true
  } catch {
    // Cross-window notification is best effort.
  }
}

function detachListenerIfUnused(): void {
  if (!listenerAttached || listeners.size) return
  try {
    globalThis.removeEventListener?.('storage', handleStorageEvent)
  } catch {
    // Ignore teardown failures.
  }
  listenerAttached = false
}

// Every write must store a token that differs from the one already there: browsers
// only fire `storage` when the value actually changes, so two writes made in the
// same millisecond (or by two windows that happen to share that millisecond) would
// otherwise be invisible to the other windows and leave their drafts stale.
let writeCount = 0

function revisionToken(): string {
  writeCount += 1
  return `${Date.now()}-${writeCount}-${Math.random().toString(36).slice(2, 10)}`
}

/** Announces that this window persisted a profile for `pluginId`. */
export function bumpProfileRevision(pluginId: string): void {
  if (!pluginId) return
  const store = storage()
  if (!store) return
  try {
    store.setItem(KEY_PREFIX + pluginId, revisionToken())
  } catch {
    // Without storage there is nothing to broadcast; the local window is already
    // up to date and other windows cannot share state anyway.
  }
}

/** Observes profile writes made by other windows. */
export function subscribeProfileRevision(listener: (pluginId: string) => void): () => void {
  listeners.add(listener)
  attachListener()
  return () => {
    listeners.delete(listener)
    detachListenerIfUnused()
  }
}
