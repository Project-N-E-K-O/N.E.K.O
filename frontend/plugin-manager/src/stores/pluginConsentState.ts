import { defineStore } from 'pinia'
import { nextTick, ref, shallowRef, watch } from 'vue'
import {
  analyzePluginCapabilities,
  approvePluginConsent,
  getPluginConsent,
  rejectPluginConsent,
  revokePluginConsent,
  type ConsentSnapshot,
  type PluginConsent,
  type PluginConsentSummary,
} from '@/api/pluginConsent'

export interface ConsentRequestPlugin {
  id: string
  name: string
  version?: string
}
export interface ConsentRequest {
  plugins: ConsentRequestPlugin[]
}
interface PendingRequest extends ConsentRequest {
  key: string
  promise: Promise<boolean>
  resolve: (approved: boolean) => void
}

/**
 * Single owner of consent state: API-backed details, list-projection
 * summaries, the one analysis poller, and the confirmation dialog queue.
 */
export const usePluginConsentState = defineStore('pluginConsent', () => {
  const details = ref<Record<string, PluginConsent>>({})
  const summaries = ref<Record<string, PluginConsentSummary & { version: string }>>({})
  const versions = new Map<string, number>()
  const mutations = new Map<string, Promise<PluginConsent>>()
  async function request(id: string, operation: () => Promise<PluginConsent>) {
    const version = (versions.get(id) ?? 0) + 1
    versions.set(id, version)
    const result = await operation()
    if (versions.get(id) === version) details.value[id] = result
    return details.value[id] ?? result
  }
  function mutate(id: string, operation: () => Promise<PluginConsent>) {
    const previous = mutations.get(id)
    const pending = (previous ? previous.catch(() => undefined) : Promise.resolve()).then(() =>
      request(id, operation)
    )
    mutations.set(id, pending)
    const clear = () => {
      if (mutations.get(id) === pending) mutations.delete(id)
    }
    void pending.then(clear, clear)
    return pending
  }
  function load(id: string) {
    return mutations.get(id) ?? request(id, () => getPluginConsent(id))
  }
  function forget(id: string) {
    versions.set(id, (versions.get(id) ?? 0) + 1)
    delete details.value[id]
    delete summaries.value[id]
  }

  /** Seed summary projections from plugin list/detail payloads. */
  function seedSummaries(
    plugins: Array<{
      id: string
      version?: string
      consent?: PluginConsentSummary | null
    }>,
  ) {
    const next = { ...summaries.value }
    for (const plugin of plugins) {
      if (!plugin.consent) continue
      next[plugin.id] = {
        state: plugin.consent.state,
        decision: plugin.consent.decision ?? null,
        upgraded: plugin.consent.upgraded,
        version: plugin.version ?? '',
      }
    }
    summaries.value = next
  }

  /** Details win when their version matches the plugin version; else summary. */
  function stateOf(id: string): PluginConsentSummary | null {
    const detail = details.value[id]
    const summary = summaries.value[id]
    if (detail && (!summary || detail.version === summary.version)) {
      return {
        state: detail.state,
        decision: detail.decision,
        upgraded: detail.upgraded,
      }
    }
    return summary ?? null
  }

  // Single poller: consumers register interest in a plugin and the store
  // refreshes it every 2s while its state is "analyzing" and the document is
  // visible.
  const watchers = new Map<string, number>()
  let pollTimer: ReturnType<typeof setInterval> | undefined
  function pollTick() {
    if (document.hidden) return
    for (const id of watchers.keys()) {
      if ((stateOf(id)?.state ?? 'analyzing') === 'analyzing') {
        void load(id).catch(() => undefined)
      }
    }
  }
  function watchPlugin(id: string) {
    watchers.set(id, (watchers.get(id) ?? 0) + 1)
    if (pollTimer === undefined) pollTimer = setInterval(pollTick, 2000)
  }
  function unwatchPlugin(id: string) {
    const remaining = (watchers.get(id) ?? 0) - 1
    if (remaining > 0) watchers.set(id, remaining)
    else watchers.delete(id)
    if (!watchers.size && pollTimer !== undefined) {
      clearInterval(pollTimer)
      pollTimer = undefined
    }
  }

  // Confirmation dialog queue. Requests with the same plugin set dedupe;
  // the active request auto-resolves when every plugin is clear.
  const activeRequest = shallowRef<ConsentRequest | null>(null)
  const requests = new Map<string, PendingRequest>()
  const queue: PendingRequest[] = []
  let current: PendingRequest | null = null
  let stopAutoResolve: (() => void) | null = null

  function advance() {
    if (current) return
    current = queue.shift() ?? null
    activeRequest.value = current ? { plugins: current.plugins } : null
    stopAutoResolve?.()
    stopAutoResolve = current
      ? watch(
          () => current!.plugins.map((plugin) => stateOf(plugin.id)?.state),
          (states) => {
            if (states.some((state) => state === undefined)) resolveConfirmation(false)
            else if (states.every((state) => state === 'clear')) resolveConfirmation(true)
          },
        )
      : null
  }
  function requestConfirmation(plugins: ConsentRequestPlugin[]): Promise<boolean> {
    const key = plugins
      .map((plugin) => plugin.id)
      .sort()
      .join(',')
    const existing = requests.get(key)
    if (existing) return existing.promise
    let resolve!: (approved: boolean) => void
    const promise = new Promise<boolean>((done) => {
      resolve = done
    })
    const pending: PendingRequest = {
      key,
      plugins: plugins.map((plugin) => ({ ...plugin })),
      promise,
      resolve,
    }
    requests.set(key, pending)
    queue.push(pending)
    advance()
    return promise
  }
  function resolveConfirmation(approved: boolean) {
    if (!current) return
    const pending = current
    current = null
    requests.delete(pending.key)
    stopAutoResolve?.()
    stopAutoResolve = null
    activeRequest.value = null
    pending.resolve(approved)
    // Let the previous dialog unmount before presenting a different request.
    void nextTick().then(advance)
  }

  return {
    details,
    summaries,
    activeRequest,
    forget,
    load,
    seedSummaries,
    stateOf,
    watchPlugin,
    unwatchPlugin,
    requestConfirmation,
    resolveConfirmation,
    approve: (id: string, snapshot: ConsentSnapshot) =>
      mutate(id, () => approvePluginConsent(id, snapshot)),
    reject: (id: string) => mutate(id, () => rejectPluginConsent(id)),
    revoke: (id: string) => mutate(id, () => revokePluginConsent(id)),
    analyze: (id: string) => mutate(id, () => analyzePluginCapabilities(id)),
  }
})
