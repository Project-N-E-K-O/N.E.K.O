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

export class PluginConsentInvalidated extends Error {}

export interface ConsentRequestPlugin {
  id: string
  name: string
  version?: string
}
export interface ConsentRequest {
  plugins: ConsentRequestPlugin[]
}
type VersionedConsentSummary = PluginConsentSummary & { version: string }

const ANALYSIS_BYPASS_MS = 30_000

function matchesSummary(detail: PluginConsent, summary: VersionedConsentSummary | undefined) {
  return (
    summary &&
    detail.version === summary.version &&
    detail.state === summary.state &&
    detail.decision === summary.decision &&
    detail.upgraded === summary.upgraded
  )
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
  const summaries = ref<Record<string, VersionedConsentSummary>>({})
  // Metadata and consent reads share the order in which their requests started.
  const analysisBypasses = new Map<string, { version: string; analysisId?: string; warnedAt: number }>()
  const forgottenAt = new Map<string, number>()
  const snapshotTickets = new Map<string, number>()
  const reads = new Map<string, Promise<PluginConsent>>()
  const mutations = new Map<string, Promise<PluginConsent>>()
  let sequence = 0

  function nextRequestTicket() {
    return ++sequence
  }

  function checkPluginLifetime(id: string, ticket: number) {
    if (ticket < (forgottenAt.get(id) ?? 0)) {
      throw new PluginConsentInvalidated('Plugin was removed during a consent request')
    }
  }

  function updateAnalysisBypass(id: string, snapshot: VersionedConsentSummary) {
    const previous = analysisBypasses.get(id)
    if (previous && (snapshot.state !== 'analyzing' || previous.version !== snapshot.version)) {
      analysisBypasses.delete(id)
    }
  }

  function allowStartDuringAnalysis(consent: PluginConsent): boolean {
    const { plugin_id: id, version, analysis } = consent
    const previous = analysisBypasses.get(id)
    const now = Date.now()
    if (
      previous?.version === version &&
      previous.analysisId === analysis.analysis_id &&
      now - previous.warnedAt < ANALYSIS_BYPASS_MS
    ) {
      analysisBypasses.delete(id)
      return true
    }
    analysisBypasses.set(id, { version, analysisId: analysis.analysis_id, warnedAt: now })
    return false
  }

  async function request(
    id: string,
    operation: () => Promise<PluginConsent>,
    mutation = false,
  ): Promise<PluginConsent> {
    const ticket = nextRequestTicket()
    let responseTicket = ticket
    let read = operation
    for (;;) {
      let result: PluginConsent
      try {
        result = await read()
      } finally {
        checkPluginLifetime(id, ticket)
      }
      const pendingMutation = mutations.get(id)
      if (!mutation && pendingMutation) return pendingMutation
      if (
        responseTicket >= (snapshotTickets.get(id) ?? 0) ||
        (mutation && matchesSummary(result, summaries.value[id]))
      ) {
        snapshotTickets.set(id, mutation ? nextRequestTicket() : responseTicket)
        updateAnalysisBypass(id, result)
        details.value[id] = result
        return result
      }
      const detail = details.value[id]
      if (!mutation && detail) return detail

      // Keep all readers waiting for the current snapshot, including when a
      // mutation has committed and its response conflicts with a newer summary.
      responseTicket = nextRequestTicket()
      read = () => getPluginConsent(id)
    }
  }
  function mutate(id: string, operation: () => Promise<PluginConsent>) {
    const queuedAt = nextRequestTicket()
    const previous = mutations.get(id)
    const pending = (previous ? previous.catch(() => undefined) : Promise.resolve()).then(() => {
      checkPluginLifetime(id, queuedAt)
      return request(id, operation, true)
    })
    mutations.set(id, pending)
    const clear = () => {
      if (mutations.get(id) === pending) mutations.delete(id)
    }
    void pending.then(clear, clear)
    return pending
  }
  function load(id: string): Promise<PluginConsent> {
    const inFlight = mutations.get(id) ?? reads.get(id)
    if (inFlight) return inFlight
    const pending = request(id, () => getPluginConsent(id))
    reads.set(id, pending)
    const clear = () => {
      if (reads.get(id) === pending) reads.delete(id)
    }
    void pending.then(clear, clear)
    return pending
  }
  function forget(id: string) {
    const ticket = nextRequestTicket()
    forgottenAt.set(id, ticket)
    snapshotTickets.set(id, ticket)
    analysisBypasses.delete(id)
    reads.delete(id)
    mutations.delete(id)
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
    ticket = nextRequestTicket(),
  ) {
    const next = { ...summaries.value }
    for (const plugin of plugins) {
      if (!plugin.consent || ticket < (snapshotTickets.get(plugin.id) ?? 0)) continue
      const summary = {
        state: plugin.consent.state,
        decision: plugin.consent.decision ?? null,
        upgraded: plugin.consent.upgraded,
        version: plugin.version ?? '',
      }
      snapshotTickets.set(plugin.id, ticket)
      updateAnalysisBypass(plugin.id, summary)
      const detail = details.value[plugin.id]
      if (detail && !matchesSummary(detail, summary)) {
        delete details.value[plugin.id]
        if (activeRequest.value?.plugins.some(({ id }) => id === plugin.id)) {
          void load(plugin.id).catch(() => undefined)
        }
      }
      next[plugin.id] = summary
    }
    summaries.value = next
  }

  /** Accepted summaries invalidate older details when their projections differ. */
  function stateOf(id: string): PluginConsentSummary | null {
    const detail = details.value[id]
    const summary = summaries.value[id]
    if (detail) {
      return {
        state: detail.state,
        decision: detail.decision,
        upgraded: detail.upgraded,
      }
    }
    return summary ?? null
  }

  function isAnalysisPending(id: string): boolean {
    return Boolean(details.value[id]?.analysis?.analysis_id) || stateOf(id)?.state === 'analyzing'
  }

  // Single poller: consumers register interest in a plugin and the store
  // refreshes missing details and pending analysis while the document is visible.
  const watchers = new Map<string, number>()
  let pollTimer: ReturnType<typeof setInterval> | undefined
  function pollTick() {
    if (document.hidden) return
    for (const id of watchers.keys()) {
      if (!reads.has(id) && (!details.value[id] || isAnalysisPending(id))) {
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
    allowStartDuringAnalysis,
    isAnalysisPending,
    forget,
    load,
    nextRequestTicket,
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
